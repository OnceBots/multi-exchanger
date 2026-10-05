from __future__ import annotations

import asyncio

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo


def build_master_router(manager) -> Router:
    router = Router(name="master")

    def is_admin(message: Message) -> bool:
        return bool(message.from_user and int(message.from_user.id) in manager.settings.admin_ids)

    def create_webapp_button() -> InlineKeyboardButton:
        return InlineKeyboardButton(text="➕ Crear mi bot", web_app=WebAppInfo(url=f"{manager.settings.app_base_url.rstrip('/')}/master-app"))

    def master_public_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [create_webapp_button()],
            [InlineKeyboardButton(text="🤖 Mis bots", callback_data="master:mine"), InlineKeyboardButton(text="📖 Cómo funciona", callback_data="master:how")],
        ])

    def admin_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Gestionar bots", callback_data="master:list"), create_webapp_button()],
            [InlineKeyboardButton(text="❤️ Salud", callback_data="master:health"), InlineKeyboardButton(text="📊 Estadísticas", callback_data="master:stats")],
            [InlineKeyboardButton(text="📖 Guía", callback_data="master:how")],
        ])

    async def begin_create(message: Message, user_id: int | None = None) -> None:
        # callback.message.from_user es el bot, no el usuario que pulsó el botón.
        # Por eso el owner de la sesión debe venir explícitamente del actor.
        actor_id = int(user_id if user_id is not None else message.from_user.id)
        await manager.repositories.session.set(0, actor_id, "master_token", {})
        await message.answer(
            "<b>➕ CREAR BOT HIJO</b>\n"
            "<i>Configuración rápida y segura</i>\n\n"
            "<b>1.</b> Abre <code>@BotFather</code> y crea tu bot.\n"
            "<b>2.</b> Copia su <b>token</b>.\n"
            "<b>3.</b> Pégalo aquí para validarlo.\n\n"
            "🔐 El token se valida y se almacena cifrado.\n"
            "⚡ Tras una validación correcta, el bot se activa automáticamente.\n\n"
            "<i>Por seguridad, elimina el mensaje del token después de enviarlo.</i>"
        )

    async def show_start(message: Message) -> None:
        uid = int(message.from_user.id)
        if uid in manager.settings.admin_ids:
            text = (
                "<b>👑 BOT MASTER</b>\n"
                "<i>Centro de control de la plataforma</i>\n\n"
                "🟢 <b>Creación pública:</b> cualquier usuario puede registrar un bot hijo.\n"
                "🧩 <b>Gestión central:</b> estado, salud, reinicios y configuración.\n"
                "🛡️ <b>Administración:</b> control y supervisión de todos los tenants.\n\n"
                "<b>Selecciona una sección para comenzar.</b>"
            )
            await message.answer(text, reply_markup=admin_kb())
        else:
            text = (
                "<b>🚀 MULTIBOT HUB</b>\n"
                "<i>Crea tu propio bot hijo en pocos minutos</i>\n\n"
                "<b>¿Cómo funciona?</b>\n"
                "1️⃣ Crea un bot con <code>@BotFather</code>.\n"
                "2️⃣ Pulsa <b>➕ Crear mi bot</b>.\n"
                "3️⃣ Pega el token y espera la validación.\n"
                "4️⃣ Tu bot quedará listo para usar.\n\n"
                "<b>✨ Incluye</b>\n"
                "🏠 Salas públicas y privadas\n"
                "📸 Fotos · 🎬 Vídeos · 📎 Archivos · 🖼️ Álbumes\n"
                "🕶️ Publicación anónima en salas\n"
                "📱 Mini App móvil\n\n"
                "<b>🔐 Seguridad</b>\n"
                "Los tokens se validan y se almacenan cifrados. No los compartas con otras personas."
            )
            await message.answer(text, reply_markup=master_public_kb())

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await show_start(message)

    @router.message(Command("add_bot"))
    async def add_bot(message: Message) -> None:
        await begin_create(message)

    @router.message(Command("bots"))
    async def bots(message: Message) -> None:
        if not is_admin(message):
            docs = await manager.repositories.bots.list_for_owner(int(message.from_user.id))
            if not docs:
                await message.answer("🤖 Aún no tienes bots hijos.", reply_markup=master_public_kb())
                return
            await _render_bot_list(message, docs, owner_view=True)
            return
        docs = await manager.repositories.bots.list_all()
        await _render_bot_list(message, docs, owner_view=False)

    async def _render_bot_list(target: Message, docs: list[dict], owner_view: bool) -> None:
        if not docs:
            await target.answer("<b>🤖 Bots</b>\n\nTodavía no hay bots registrados.", reply_markup=admin_kb() if is_admin(target) else master_public_kb())
            return
        rows = []
        for doc in docs[:30]:
            status = {"RUNNING": "🟢", "STARTING": "🟡", "RESTARTING": "🟠", "ERROR": "🔴", "STOPPED": "⚫", "DISABLED": "🔵"}.get(doc.get("status"), "⚪")
            label = f"{status} @{doc.get('username') or doc['bot_id']}"
            rows.append([InlineKeyboardButton(text=label[:40], callback_data=f"master:botinfo:{doc['bot_id']}")])
        rows.append([create_webapp_button()])
        rows.append([InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")])
        await target.answer(f"<b>{'🤖 Mis bots' if owner_view else '🤖 Todos los bots'}</b>\n\nSelecciona un bot:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.message(Command("bot_info"))
    async def bot_info(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Uso: /bot_info ID")
            return
        await send_info(message, int(parts[1]))

    async def send_info(target, bot_id: int) -> None:
        try:
            info = await manager.get_info(bot_id)
        except Exception as exc:
            await target.answer(f"❌ {html.escape(str(exc))}")
            return
        uid = int(target.from_user.id)
        allowed = uid in manager.settings.admin_ids or uid == info.owner_id
        if not allowed:
            await target.answer("⛔ No puedes administrar este bot.")
            return
        runtime = manager.registry.get(bot_id)
        feed_count = await manager.repositories.admin_feed.count(bot_id)
        queue = runtime.broadcast_queue.qsize() if runtime and runtime.broadcast_queue else 0
        status_icon = {"RUNNING": "🟢", "ERROR": "🔴", "STOPPED": "⚫", "STARTING": "🟡", "RESTARTING": "🟠"}.get(str(info.status), "⚪")
        text = (
            f"<b>🤖 @{html.escape(info.username or str(info.bot_id))}</b>\n\n"
            f"{status_icon} Estado: <b>{info.status}</b>\n"
            f"🆔 <code>{info.bot_id}</code>\n"
            f"👤 Owner: <code>{info.owner_id}</code>\n"
            f"🛰 Admin Feed activos: <b>{feed_count}</b>\n"
            f"📦 Cola: <b>{queue}</b>\n"
            f"🔄 Reinicios: <b>{info.restart_count}</b>\n"
            f"❤️ Heartbeat: <b>{info.last_heartbeat or '—'}</b>"
        )
        rows = []
        if info.username:
            rows.append([InlineKeyboardButton(text="🤖 Abrir bot", url=f"https://t.me/{info.username}")])
        if uid in manager.settings.admin_ids:
            rows.extend([
                [InlineKeyboardButton(text="▶️ Iniciar", callback_data=f"master:start:{bot_id}"), InlineKeyboardButton(text="⏹ Detener", callback_data=f"master:stop:{bot_id}")],
                [InlineKeyboardButton(text="🔄 Reiniciar", callback_data=f"master:restart:{bot_id}"), InlineKeyboardButton(text="📊 Salud", callback_data=f"master:healthbot:{bot_id}")],
                [InlineKeyboardButton(text="🗑 Eliminar", callback_data=f"master:delete:{bot_id}")],
            ])
        rows.append([InlineKeyboardButton(text="↩️ Bots", callback_data="master:list")])
        await target.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    async def perform(message_or_callback, action: str, bot_id: int) -> None:
        if isinstance(message_or_callback, Message):
            actor = int(message_or_callback.from_user.id)
        else:
            actor = int(message_or_callback.from_user.id)
        if actor not in manager.settings.admin_ids:
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.answer("Solo administradores.", show_alert=True)
            else:
                await message_or_callback.answer("⛔ Solo administradores.")
            return
        try:
            result = await getattr(manager, action)(bot_id)
            text = f"✅ {html.escape(str(result))}"
        except Exception as exc:
            text = f"❌ {type(exc).__name__}: {html.escape(str(exc)[:300])}"
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.answer(text[:190], show_alert=True)
            await send_info(message_or_callback.message, bot_id)
        else:
            await message_or_callback.answer(text)

    @router.callback_query(F.data == "master:create")
    async def create_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await begin_create(callback.message, int(callback.from_user.id))

    @router.callback_query(F.data == "master:home")
    async def home_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        if int(callback.from_user.id) in manager.settings.admin_ids:
            await callback.message.edit_text("<b>👑 BOT MASTER</b>\n<i>Centro de control</i>\n\nSelecciona una sección.", reply_markup=admin_kb())
        else:
            await callback.message.edit_text("<b>🚀 MULTIBOT HUB</b>\n<i>Selecciona una opción</i>", reply_markup=master_public_kb())

    @router.callback_query(F.data == "master:mine")
    async def mine_callback(callback: CallbackQuery) -> None:
        docs = await manager.repositories.bots.list_for_owner(int(callback.from_user.id))
        await callback.answer()
        await callback.message.edit_text("<b>🤖 Mis bots</b>\n\nSelecciona un bot:" if docs else "🤖 Todavía no tienes bots.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{'🟢' if d.get('status') == 'RUNNING' else '⚪'} @{d.get('username') or d['bot_id']}", callback_data=f"master:botinfo:{d['bot_id']}")] for d in docs[:20]] + [[create_webapp_button()], [InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")]]))

    @router.callback_query(F.data == "master:list")
    async def list_callback(callback: CallbackQuery) -> None:
        if int(callback.from_user.id) not in manager.settings.admin_ids:
            await callback.answer("Solo administradores.", show_alert=True); return
        docs = await manager.repositories.bots.list_all()
        await callback.answer()
        if not docs:
            await callback.message.edit_text("<b>🤖 Todos los bots</b>\n\nNo hay bots registrados.", reply_markup=admin_kb()); return
        rows = [[InlineKeyboardButton(text=f"{'🟢' if d.get('status') == 'RUNNING' else '🔴' if d.get('status') == 'ERROR' else '⚪'} @{d.get('username') or d['bot_id']}", callback_data=f"master:botinfo:{d['bot_id']}")] for d in docs[:30]]
        rows.append([create_webapp_button(), InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")])
        await callback.message.edit_text("<b>🤖 Todos los bots</b>\n\nSelecciona un bot:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("master:botinfo:"))
    async def botinfo_callback(callback: CallbackQuery) -> None:
        bot_id = int(callback.data.split(":")[-1])
        await callback.answer()
        await send_info(callback.message, bot_id)

    @router.callback_query(F.data.startswith("master:start:"))
    async def start_bot_callback(callback: CallbackQuery) -> None:
        await perform(callback, "bot_start", int(callback.data.split(":")[-1]))

    @router.callback_query(F.data.startswith("master:stop:"))
    async def stop_bot_callback(callback: CallbackQuery) -> None:
        await perform(callback, "bot_stop", int(callback.data.split(":")[-1]))

    @router.callback_query(F.data.startswith("master:restart:"))
    async def restart_bot_callback(callback: CallbackQuery) -> None:
        await perform(callback, "bot_restart", int(callback.data.split(":")[-1]))

    @router.callback_query(F.data.startswith("master:delete:"))
    async def delete_bot_callback(callback: CallbackQuery) -> None:
        bot_id = int(callback.data.split(":")[-1])
        if int(callback.from_user.id) not in manager.settings.admin_ids:
            await callback.answer("Solo administradores.", show_alert=True); return
        await callback.answer("Confirma eliminación", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ Sí, eliminar", callback_data=f"master:delete_confirm:{bot_id}")], [InlineKeyboardButton(text="↩️ Cancelar", callback_data=f"master:botinfo:{bot_id}")]]))

    @router.callback_query(F.data.startswith("master:delete_confirm:"))
    async def delete_confirm(callback: CallbackQuery) -> None:
        await perform(callback, "bot_delete", int(callback.data.split(":")[-1]))

    @router.callback_query(F.data == "master:health")
    async def health(callback: CallbackQuery) -> None:
        if int(callback.from_user.id) not in manager.settings.admin_ids:
            await callback.answer("Solo administradores.", show_alert=True); return
        lines = ["<b>❤️ Salud de la plataforma</b>", ""]
        for bot_id, runtime in manager.registry.items():
            q1 = runtime.broadcast_queue.qsize() if runtime.broadcast_queue else 0
            q2 = runtime.admin_feed_queue.qsize() if runtime.admin_feed_queue else 0
            lines.append(f"{'🟢' if str(runtime.status) == 'RUNNING' else '🔴'} <code>{bot_id}</code> · {runtime.status} · cola {q1 + q2}")
        await callback.answer()
        await callback.message.edit_text("\n".join(lines) if len(lines) > 2 else "<b>❤️ Salud</b>\n\nNo hay runtimes activos.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")]]))

    @router.callback_query(F.data.startswith("master:healthbot:"))
    async def healthbot(callback: CallbackQuery) -> None:
        if int(callback.from_user.id) not in manager.settings.admin_ids:
            await callback.answer("Solo administradores.", show_alert=True); return
        bot_id = int(callback.data.split(":")[-1])
        runtime = manager.registry.get(bot_id)
        await callback.answer()
        if not runtime:
            await callback.message.answer("⚫ El runtime no está activo."); return
        await callback.message.answer(f"<b>❤️ Health {bot_id}</b>\n\nEstado: {runtime.status}\nTasks: {runtime.task_registry.count}\nBroadcast: {runtime.broadcast_queue.qsize() if runtime.broadcast_queue else 0}\nAdmin feed: {runtime.admin_feed_queue.qsize() if runtime.admin_feed_queue else 0}")

    @router.callback_query(F.data == "master:stats")
    async def stats_callback(callback: CallbackQuery) -> None:
        if int(callback.from_user.id) not in manager.settings.admin_ids:
            await callback.answer("Solo administradores.", show_alert=True); return
        docs = await manager.repositories.bots.list_all()
        running = sum(1 for d in docs if d.get("status") == "RUNNING")
        enabled = sum(1 for d in docs if d.get("enabled"))
        await callback.answer()
        await callback.message.edit_text(f"<b>📊 Estadísticas</b>\n\n🤖 Bots: <b>{len(docs)}</b>\n🟢 Running: <b>{running}</b>\n✅ Habilitados: <b>{enabled}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")]]))

    @router.callback_query(F.data == "master:how")
    async def how_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await callback.message.edit_text(
            "<b>📖 CÓMO FUNCIONA</b>\n"
            "<i>Una plataforma para crear y administrar bots Telegram sin duplicar infraestructura</i>\n\n"
            "<b>👤 Creadores</b>\n"
            "Cualquier usuario puede registrar un bot hijo con un token válido de <code>@BotFather</code>.\n\n"
            "<b>👑 Administradores</b>\n"
            "Supervisan el estado, salud y actividad de los bots registrados.\n\n"
            "<b>🏠 Salas</b>\n"
            "Comunidades públicas y privadas con permisos, moderación y difusión anónima.\n\n"
            "<b>🔐 Privacidad</b>\n"
            "El contenido de un bot puede ser revisado por la administración para moderación, seguridad y cumplimiento.\n\n"
            "<b>📱 Mini App</b>\n"
            "La gestión de salas y perfiles también está disponible desde una interfaz móvil.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")]]),
        )

    @router.message()
    async def master_session(message: Message) -> None:
        if not message.text or message.text.startswith("/") or not message.from_user:
            return
        user_id = int(message.from_user.id)
        session = await manager.repositories.session.get(0, user_id)
        if not session or session.get("step") != "master_token":
            return

        token = message.text.strip()
        await manager.repositories.session.clear(0, user_id)

        # Dar respuesta inmediata para que el usuario no quede esperando
        # mientras Telegram valida el token y se configura el child bot.
        status_message = await message.answer(
            "<b>⏳ VALIDANDO BOT</b>\n\n"
            "Estoy comprobando el token con Telegram y preparando tu bot hijo.\n\n"
            "<i>Este proceso puede tardar unos segundos.</i>"
        )

        # El token ya no debe quedar expuesto en el chat.
        try:
            await message.delete()
        except Exception:
            pass

        try:
            info = await asyncio.wait_for(
                manager.register_bot(token, user_id),
                timeout=60.0,
            )
            await status_message.edit_text(
                "<b>✅ BOT HIJO CREADO</b>\n\n"
                f"🤖 <b>@{html.escape(info.username or str(info.bot_id))}</b>\n"
                f"🆔 <code>{info.bot_id}</code>\n"
                "🟢 <b>Estado:</b> Activo\n\n"
                "Tu bot ya está listo para utilizarse.\n"
                "Puedes abrirlo desde el botón inferior.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        ([InlineKeyboardButton(text="🤖 Abrir mi bot", url=f"https://t.me/{info.username}")] if info.username else []),
                        [create_webapp_button()],
                        [InlineKeyboardButton(text="↩️ Volver al inicio", callback_data="master:home")],
                    ]
                ),
            )
        except asyncio.TimeoutError:
            await status_message.edit_text(
                "<b>⏱️ VALIDACIÓN EN CURSO</b>\n\n"
                "Telegram está tardando más de lo habitual en responder.\n\n"
                "He detenido la espera del chat para que no quede bloqueado. "
                "Comprueba <b>Mis bots</b> en unos instantes antes de volver a intentarlo."
            )
        except Exception as exc:
            await status_message.edit_text(
                "<b>❌ NO SE PUDO CREAR EL BOT</b>\n\n"
                f"<code>{html.escape(str(exc)[:500])}</code>\n\n"
                "Verifica que el token sea correcto y vuelve a intentarlo.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [create_webapp_button()],
                        [InlineKeyboardButton(text="↩️ Inicio", callback_data="master:home")],
                    ]
                ),
            )

    @router.message(Command("stats"))
    async def stats_cmd(message: Message) -> None:
        if not is_admin(message):
            await message.answer("<b>📊 Estadísticas</b>\n\nEsta sección está disponible únicamente para administradores.")
            return
        docs = await manager.repositories.bots.list_all()
        running = sum(1 for d in docs if d.get("status") == "RUNNING")
        enabled = sum(1 for d in docs if d.get("enabled"))
        await message.answer(f"<b>📊 Estadísticas</b>\n\n🤖 Bots: <b>{len(docs)}</b>\n🟢 Running: <b>{running}</b>\n✅ Habilitados: <b>{enabled}</b>", reply_markup=admin_kb())

    return router
