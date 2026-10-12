from __future__ import annotations

import asyncio
import html
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


from app.services.token_service import InvalidBotTokenError
from app.services.bot_lifecycle import botfather_instructions, schedule_label
from app.services.thumbnail_settings import save_default_video_thumbnail




async def _safe_callback_answer(callback: CallbackQuery, *args, **kwargs) -> None:
    """Answer a callback without turning an expired query into a webhook failure."""
    try:
        await callback.answer(*args, **kwargs)
    except Exception as exc:
        message = str(exc).lower()
        if "query is too old" in message or "query id is invalid" in message or "response timeout expired" in message:
            return
        raise
def build_master_router(manager) -> Router:
    router = Router(name="master")

    def is_admin(user_id: int) -> bool:
        return user_id in manager.settings.admin_ids

    def is_super_admin(user_id: int) -> bool:
        # Global Master settings are authorized by the configured ADMIN_IDS.
        return user_id in manager.settings.admin_ids

    def menu(user_id: int) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton(text="➕ Crear mi bot", callback_data="master:create")],
            [InlineKeyboardButton(text="🤖 Mis bots", callback_data="master:mine"), InlineKeyboardButton(text="📖 Cómo funciona", callback_data="master:how")],
        ]
        if is_admin(user_id):
            rows.append([InlineKeyboardButton(text="🛡️ Administración", callback_data="master:list")])
        if is_super_admin(user_id):
            rows.append([InlineKeyboardButton(text="⚙️ Configuración global", callback_data="master:global-config")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def begin_create(target: Message, user_id: int) -> None:
        await manager.repositories.session.set(0, user_id, "create_child_token", {})
        await target.answer(
            "<b>➕ CREAR BOT HIJO</b>\n\n"
            "1️⃣ Crea un bot con <code>@BotFather</code>.\n"
            "2️⃣ Envía aquí el token.\n"
            "3️⃣ Lo validaremos con Telegram.\n"
            "4️⃣ El bot se registrará, cifrará y activará con webhook.\n\n"
            "🔐 El token no se mostrará en los logs. El mensaje se elimina después de recibirlo."
        )

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        uid = int(message.from_user.id)
        if uid in manager.settings.admin_ids:
            text = (
                "<b>👑 MASTER CONTROL</b>\n\n"
                "🟢 Multi-bot activo\n"
                "🔐 Tokens cifrados\n"
                "🌐 Webhooks individuales\n"
                "🧩 Cada bot tiene runtime aislado\n\n"
                "Usa los botones de este chat para administrar la plataforma."
            )
        else:
            text = (
                "<b>🚀 MULTIBOT HUB</b>\n\n"
                "Crea tu propio bot hijo sin configurar servidores ni polling.\n\n"
                "✨ Salas públicas/privadas\n"
                "📸 Fotos · 🎬 Vídeos · 📎 Archivos · 🎞 GIF · 🖼 Álbumes\n"
                "🕶️ Distribución anónima\n"
                "🔐 Los tokens se validan y se almacenan cifrados."
            )
        await message.answer(text, reply_markup=menu(uid))

    @router.message(Command("add_bot"))
    async def add_bot(message: Message) -> None:
        await begin_create(message, int(message.from_user.id))

    @router.message(Command("bots"))
    async def bots(message: Message) -> None:
        uid = int(message.from_user.id)
        docs = await manager.repositories.bots.list_all() if is_admin(uid) else await manager.repositories.bots.list_for_owner(uid)
        if not docs:
            await message.answer("<b>🤖 Bots</b>\n\nNo hay bots registrados.", reply_markup=menu(uid))
            return
        await render_list(message, docs)

    async def render_list(target: Message, docs: list[dict]) -> None:
        uid = int(target.from_user.id)
        rows = []
        for doc in docs[:30]:
            icons = {"RUNNING": "🟢", "STARTING": "🟡", "RESTARTING": "🟠", "ERROR": "🔴", "STOPPED": "⚫", "DISABLED": "🔵"}
            label = f"{icons.get(doc.get('status'), '⚪')} @{doc.get('username') or doc.get('bot_id')}"
            rows.append([InlineKeyboardButton(text=label[:42], callback_data=f"master:botinfo:{doc['bot_id']}")])
        rows.append([InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")])
        await target.answer("<b>🤖 Bots</b>\n\nSelecciona uno:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    async def show_info(target, bot_id: int) -> None:
        info = await manager.get_info(bot_id)
        uid = int(target.from_user.id)
        if uid not in manager.settings.admin_ids and uid != info.owner_id:
            await target.answer("⛔ No puedes administrar este bot.")
            return
        runtime = manager.registry.get(bot_id)
        status = runtime.status.value if runtime else info.status
        queue = runtime.broadcast_queue.qsize() if runtime and runtime.broadcast_queue else 0
        lifecycle = (await manager.repositories.bots.get(bot_id) or {}).get("config", {}).get("lifecycle") or {}
        timer_line = "⏱️ Modo: <b>Manual</b>"
        if lifecycle.get("mode") == "TIMER" and lifecycle.get("delete_at"):
            timer_line = f"⏱️ Vence: <code>{html.escape(str(lifecycle.get('delete_at')))}</code>"
        text = (
            f"<b>🤖 @{html.escape(info.username or str(info.bot_id))}</b>\n\n"
            f"🟢 Estado: <b>{html.escape(status)}</b>\n"
            f"🆔 <code>{info.bot_id}</code>\n"
            f"👤 Owner: <code>{info.owner_id}</code>\n"
            f"{timer_line}\n"
            f"📦 Cola: <b>{queue}</b>\n"
            f"🔄 Reinicios: <b>{info.restart_count}</b>\n"
            f"❤️ Heartbeat: <code>{html.escape(str(info.last_heartbeat or '—'))}</code>"
        )
        rows = []
        if info.username:
            rows.append([InlineKeyboardButton(text="🤖 Abrir bot", url=f"https://t.me/{info.username}")])
        if uid == info.owner_id or uid in manager.settings.admin_ids:
            rows.append([InlineKeyboardButton(text="⏱️ Temporizador", callback_data=f"master:timer:{bot_id}")])
            rows.append([InlineKeyboardButton(text="📖 Eliminar en BotFather", callback_data=f"master:deletehelp:{bot_id}")])
        if uid in manager.settings.admin_ids:
            rows.extend([
                [InlineKeyboardButton(text="▶️ Iniciar", callback_data=f"master:start:{bot_id}"), InlineKeyboardButton(text="⏹ Detener", callback_data=f"master:stop:{bot_id}")],
                [InlineKeyboardButton(text="🔄 Reiniciar", callback_data=f"master:restart:{bot_id}"), InlineKeyboardButton(text="🗑 Eliminar", callback_data=f"master:delete:{bot_id}")],
            ])
        rows.append([InlineKeyboardButton(text="↩️ Bots", callback_data="master:list")])
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            await target.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data == "master:home")
    async def home(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await callback.message.edit_text("<b>🚀 MULTIBOT HUB</b>\n\nSelecciona una opción:", reply_markup=menu(int(callback.from_user.id)))

    @router.callback_query(F.data == "master:how")
    async def how(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await callback.message.edit_text(
            "<b>📖 Cómo funciona</b>\n\n"
            "1. Creas tu bot en @BotFather.\n"
            "2. Lo registras en el Master.\n"
            "3. El Master valida el token con Telegram.\n"
            "4. Se crea un runtime independiente y su webhook.\n"
            "5. Tu bot puede gestionar sus propias salas y multimedia.\n\n"
            "Un fallo en un child no detiene al Master ni a los demás bots.",
            reply_markup=menu(int(callback.from_user.id)),
        )

    @router.callback_query(F.data == "master:mine")
    async def mine(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        docs = await manager.repositories.bots.list_for_owner(int(callback.from_user.id))
        if not docs:
            await callback.message.edit_text("<b>🤖 Mis bots</b>\n\nTodavía no tienes bots.", reply_markup=menu(int(callback.from_user.id)))
            return
        rows = [[InlineKeyboardButton(text=f"🤖 @{doc.get('username') or doc['bot_id']}", callback_data=f"master:botinfo:{doc['bot_id']}")] for doc in docs[:30]]
        rows.append([InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")])
        await callback.message.edit_text("<b>🤖 Mis bots</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    async def request_thumbnail(target: Message, user_id: int) -> None:
        if not is_super_admin(user_id):
            await target.answer("⛔ Solo el superadministrador puede cambiar la miniatura.")
            return
        await manager.repositories.session.set(0, user_id, "set_video_thumbnail", {})
        await target.answer(
            "<b>🖼️ Miniatura predeterminada de videos</b>\n\n"
            "Enviá ahora la imagen como <b>foto</b> (no como archivo).\n"
            "Se ajustará automáticamente a JPG, máximo 320×320 y menos de 200 KB.\n\n"
            "La miniatura será global para los bots hijos y se aplicará a las próximas subidas de video."
        )

    @router.message(Command("storage_channel"))
    async def storage_channel_command(message: Message) -> None:
        uid = int(message.from_user.id) if message.from_user else 0
        if not is_super_admin(uid):
            await message.answer("⛔ Solo el superadministrador puede configurar el canal.")
            return
        await manager.repositories.session.set(0, uid, "set_storage_channel", {})
        await message.answer("Enviá el ID numérico del canal de almacenamiento (por ejemplo, -1001234567890).")

    @router.message(Command("thumbnail"))
    async def thumbnail_command(message: Message) -> None:
        if message.from_user:
            await request_thumbnail(message, int(message.from_user.id))

    @router.callback_query(F.data == "master:thumbnail")
    async def thumbnail_callback(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await request_thumbnail(callback.message, int(callback.from_user.id))

    @router.message(F.photo)
    async def thumbnail_photo(message: Message) -> None:
        if not message.from_user:
            return
        uid = int(message.from_user.id)
        session = await manager.repositories.session.get(0, uid)
        if not session or session.get("step") != "set_video_thumbnail":
            return
        if not is_super_admin(uid):
            await manager.repositories.session.clear(0, uid)
            await message.answer("⛔ Solo el superadministrador puede cambiar la miniatura.")
            return
        try:
            photo = message.photo[-1]
            image_buffer = BytesIO()
            file_info = await manager.master_bot.get_file(photo.file_id)
            file_path = file_info.file_path
            if not file_path:
                raise ValueError("Telegram no devolvió la ruta de la imagen.")

            if manager.settings.telegram_api_local_mode:
                # In local Bot API mode getFile may return a relative path such as
                # "photos/file_1.jpg". Resolve it against the configured Bot API
                # data root, then map the same relative location to the host-visible
                # root. Do not build an HTTP /file URL: this deployment's local file
                # paths are already available through the shared data directory.
                from pathlib import Path

                server_root = Path(
                    manager.settings.telegram_api_server_files_root
                ).expanduser().resolve()
                host_root = Path(
                    manager.settings.telegram_api_host_files_root
                ).expanduser().resolve()
                returned_path = Path(file_path)
                server_path = (
                    returned_path if returned_path.is_absolute()
                    else server_root / returned_path
                ).resolve()
                try:
                    relative_path = server_path.relative_to(server_root)
                except ValueError as exc:
                    raise ValueError(
                        "La ruta devuelta por el Bot API queda fuera de su directorio de datos."
                    ) from exc

                local_path = (host_root / relative_path).resolve()
                try:
                    local_path.relative_to(host_root)
                except ValueError as exc:
                    raise ValueError(
                        "La ruta de la imagen queda fuera del directorio local permitido."
                    ) from exc

                if not local_path.is_file():
                    raise FileNotFoundError(
                        "No encuentro la foto en el volumen compartido del Bot API: "
                        f"{relative_path.as_posix()}. Verificá TELEGRAM_API_SERVER_FILES_ROOT "
                        "y TELEGRAM_API_HOST_FILES_ROOT."
                    )
                if local_path.stat().st_size > 10 * 1024 * 1024:
                    raise ValueError("La imagen supera el límite de 10 MB.")
                data = await asyncio.to_thread(local_path.read_bytes)
                if len(data) > 10 * 1024 * 1024:
                    raise ValueError("La imagen supera el límite de 10 MB.")
                image_buffer.write(data)
            else:
                await manager.master_bot.download(photo.file_id, destination=image_buffer)

            saved_path = save_default_video_thumbnail(
                image_buffer.getvalue(), manager.settings.video_thumbnail_path
            )
            await manager.repositories.session.clear(0, uid)
            await message.answer(
                "✅ <b>Miniatura actualizada</b>\n\n"
                "Los próximos videos procesados por los bots hijos usarán esta imagen.\n"
                f"📁 Ruta configurada: <code>{html.escape(str(saved_path))}</code>\n\n"
                "Los videos ya subidos a Telegram no se modifican retroactivamente."
            )
        except Exception as exc:
            await message.answer(
                "❌ No pude guardar esa imagen. Probá enviarla como foto JPG/PNG.\n"
                f"Detalle: <code>{html.escape(str(exc)[:250])}</code>"
            )

    @router.callback_query(F.data == "master:global-config")
    async def global_config(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if not is_super_admin(uid):
            await _safe_callback_answer(callback, "Solo el superadministrador", show_alert=True)
            return
        await _safe_callback_answer(callback)
        current = str(manager.settings.storage_channel_id or "No configurado")
        rows = [
            [InlineKeyboardButton(text="📦 Configurar canal de almacenamiento", callback_data="master:storage-channel")],
            [InlineKeyboardButton(text="🖼️ Cambiar miniatura de videos", callback_data="master:thumbnail")],
            [InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")],
        ]
        await callback.message.edit_text(
            "<b>⚙️ CONFIGURACIÓN GLOBAL</b>\n\n"
            f"📦 Canal actual: <code>{html.escape(current)}</code>\n"
            "🖼️ Miniatura: compartida por todos los bots hijos.\n\n"
            "El canal se guarda en MongoDB y no requiere editar .env ni reiniciar. "
            "Cada bot hijo que suba videos debe tener permisos de administrador en ese canal.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @router.callback_query(F.data == "master:storage-channel")
    async def storage_channel_request(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if not is_super_admin(uid):
            await _safe_callback_answer(callback, "Solo el superadministrador", show_alert=True)
            return
        await _safe_callback_answer(callback)
        await manager.repositories.session.set(0, uid, "set_storage_channel", {})
        await callback.message.answer(
            "<b>📦 Canal de almacenamiento</b>\n\n"
            "Enviá el ID numérico del canal (normalmente empieza con <code>-100</code>).\n"
            "Agregá todos los bots hijos que vayan a procesar videos como administradores del canal, con permiso para publicar."
        )

    @router.callback_query(F.data == "master:list")
    async def admin_list(callback: CallbackQuery) -> None:
        if not is_admin(int(callback.from_user.id)):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        await _safe_callback_answer(callback)
        docs = await manager.repositories.bots.list_all()
        rows = [[InlineKeyboardButton(text=f"{doc.get('status','?')} · @{doc.get('username') or doc['bot_id']}", callback_data=f"master:botinfo:{doc['bot_id']}")] for doc in docs[:50]]
        rows.append([InlineKeyboardButton(text="➕ Crear bot", callback_data="master:create"), InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")])
        await callback.message.edit_text("<b>🛡️ Todos los bots</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data == "master:create")
    async def create_callback(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await begin_create(callback.message, int(callback.from_user.id))

    @router.callback_query(F.data.startswith("master:botinfo:"))
    async def info_callback(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await show_info(callback, int(callback.data.split(":")[-1]))

    async def admin_action(callback: CallbackQuery, action: str, bot_id: int) -> None:
        if not is_admin(int(callback.from_user.id)):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        try:
            if action == "start":
                result = await manager.start_bot(bot_id)
            elif action == "stop":
                result = await manager.stop_bot(bot_id)
            elif action == "restart":
                result = await manager.restart_bot(bot_id, reason="manual")
            elif action == "delete":
                await manager.archive_bot(bot_id, reason="manual")
                result = "Bot desactivado en la plataforma. Revisa el mensaje con los pasos de @BotFather."
                await _safe_callback_answer(callback, result, show_alert=False)
                await callback.message.edit_text("🗑️ Bot eliminado.", reply_markup=menu(int(callback.from_user.id)))
                return
            else:
                raise ValueError("Acción inválida")
            await _safe_callback_answer(callback, result, show_alert=False)
        except Exception as exc:
            await _safe_callback_answer(callback, str(exc)[:180], show_alert=True)
        await show_info(callback, bot_id)

    @router.callback_query(F.data.startswith("master:timer:"))
    async def timer_menu(callback: CallbackQuery) -> None:
        bot_id = int(callback.data.split(":")[-1])
        info = await manager.get_info(bot_id)
        uid = int(callback.from_user.id)
        if uid != info.owner_id and not is_admin(uid):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        await _safe_callback_answer(callback)
        rows = [
            [InlineKeyboardButton(text="5 min", callback_data=f"master:timer-set:{bot_id}:5"), InlineKeyboardButton(text="15 min", callback_data=f"master:timer-set:{bot_id}:15")],
            [InlineKeyboardButton(text="30 min", callback_data=f"master:timer-set:{bot_id}:30"), InlineKeyboardButton(text="1 hora", callback_data=f"master:timer-set:{bot_id}:60")],
            [InlineKeyboardButton(text="6 horas", callback_data=f"master:timer-set:{bot_id}:360"), InlineKeyboardButton(text="12 horas", callback_data=f"master:timer-set:{bot_id}:720")],
            [InlineKeyboardButton(text="24 horas", callback_data=f"master:timer-set:{bot_id}:1440"), InlineKeyboardButton(text="48 horas", callback_data=f"master:timer-set:{bot_id}:2880")],
            [InlineKeyboardButton(text="72 horas", callback_data=f"master:timer-set:{bot_id}:4320"), InlineKeyboardButton(text="♾️ Manual", callback_data=f"master:timer-set:{bot_id}:0")],
            [InlineKeyboardButton(text="↩️ Volver", callback_data=f"master:botinfo:{bot_id}")],
        ]
        await callback.message.edit_text("<b>⏱️ Temporizador del bot</b>\n\nAl vencer, el runtime se detendrá y el contenido persistido no se borrará. Recibirás instrucciones de @BotFather.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("master:timer-set:"))
    async def timer_set(callback: CallbackQuery) -> None:
        _, _, bot_id_s, minutes_s = callback.data.split(":")
        bot_id, minutes = int(bot_id_s), int(minutes_s)
        info = await manager.get_info(bot_id)
        uid = int(callback.from_user.id)
        if uid != info.owner_id and not is_admin(uid):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        result = await manager.schedule_bot_expiration(bot_id, minutes, uid)
        await _safe_callback_answer(callback, result[:190], show_alert=False)
        await show_info(callback, bot_id)

    @router.callback_query(F.data.startswith("master:deletehelp:"))
    async def delete_help(callback: CallbackQuery) -> None:
        bot_id = int(callback.data.split(":")[-1])
        info = await manager.get_info(bot_id)
        uid = int(callback.from_user.id)
        if uid != info.owner_id and not is_admin(uid):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        text, markup = botfather_instructions(info.username, reason="manual")
        await _safe_callback_answer(callback)
        await callback.message.answer(text, reply_markup=markup)

    @router.callback_query(F.data.startswith("master:start:"))
    async def start_callback(callback: CallbackQuery) -> None:
        await admin_action(callback, "start", int(callback.data.split(":")[-1]))

    @router.callback_query(F.data.startswith("master:stop:"))
    async def stop_callback(callback: CallbackQuery) -> None:
        await admin_action(callback, "stop", int(callback.data.split(":")[-1]))

    @router.callback_query(F.data.startswith("master:restart:"))
    async def restart_callback(callback: CallbackQuery) -> None:
        await admin_action(callback, "restart", int(callback.data.split(":")[-1]))

    @router.callback_query(F.data.startswith("master:delete:"))
    async def delete_callback(callback: CallbackQuery) -> None:
        await admin_action(callback, "delete", int(callback.data.split(":")[-1]))

    @router.message(Command("bot_info"))
    async def bot_info(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Uso: /bot_info ID")
            return
        await show_info(message, int(parts[1]))

    @router.message()
    async def session_messages(message: Message) -> None:
        if not message.from_user or not message.text:
            return
        if message.text.startswith("/"):
            return
        uid = int(message.from_user.id)
        session = await manager.repositories.session.get(0, uid)
        if not session:
            await message.answer("Usa /start para abrir el panel.", reply_markup=menu(uid))
            return
        if session.get("step") == "set_storage_channel":
            if not is_super_admin(uid):
                await manager.repositories.session.clear(0, uid)
                await message.answer("⛔ No autorizado.")
                return
            try:
                await manager.set_storage_channel(message.text.strip())
                await manager.repositories.session.clear(0, uid)
                await message.answer(
                    "✅ <b>Canal de almacenamiento guardado</b>\n\n"
                    f"ID: <code>{html.escape(str(manager.settings.storage_channel_id))}</code>\n"
                    "La configuración se guardó en MongoDB y ya está disponible para los bots hijos. "
                    "Recordá dar permisos de administrador a cada bot hijo en ese canal."
                )
            except Exception as exc:
                await message.answer(f"❌ {html.escape(str(exc)[:250])}")
            return
        if session.get("step") != "create_child_token":
            return
        token = message.text.strip()
        try:
            # Delete the secret-bearing message as early as possible.
            try:
                await message.delete()
            except Exception:
                pass
            info = await manager.register_bot(token, uid, metadata={"created_from": "master_chat"})
            await manager.repositories.session.clear(0, uid)
            await manager.repositories.audit.log(0, uid, "BOT_CREATED", target=str(info.bot_id), details={"username": info.username})
            await message.answer(
                "<b>🎉 BOT CREADO Y ACTIVO</b>\n\n"
                f"🤖 <b>@{html.escape(info.username or str(info.bot_id))}</b>\n"
                f"🆔 <code>{info.bot_id}</code>\n"
                "🟢 Webhook configurado\n"
                "🧩 Runtime independiente\n\n"
                "Tu bot ya está listo.",
                reply_markup=menu(uid),
            )
        except InvalidBotTokenError as exc:
            await manager.repositories.session.clear(0, uid)
            await message.answer(f"❌ {html.escape(str(exc))}")
        except Exception as exc:
            await manager.repositories.session.clear(0, uid)
            await message.answer(f"❌ No se pudo activar el bot: {html.escape(str(exc)[:500])}")

    return router
