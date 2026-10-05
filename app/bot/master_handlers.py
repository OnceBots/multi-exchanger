from __future__ import annotations

import time
import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from urllib.parse import urlencode

from app.services.webapp_auth import make_launch_token

from app.services.token_service import InvalidBotTokenError




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

    def master_app_url(user_id: int) -> str:
        token = make_launch_token(
            manager.settings.webhook_secret,
            0,
            user_id,
            manager.settings.webapp_launch_ttl_seconds,
        )
        params = {
            "user_id": str(user_id),
            "v": str(int(time.time())),
            "launch": token,
        }
        return f"{manager.settings.app_base_url}/master-app?{urlencode(params)}"

    def menu(user_id: int) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton(text="➕ Crear mi bot", web_app=WebAppInfo(url=master_app_url(user_id)))],
            [InlineKeyboardButton(text="🤖 Mis bots", callback_data="master:mine"), InlineKeyboardButton(text="📖 Cómo funciona", callback_data="master:how")],
        ]
        if is_admin(user_id):
            rows.append([InlineKeyboardButton(text="🛡️ Administración", callback_data="master:list")])
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
                "Usa el panel para administrar la plataforma."
            )
        else:
            text = (
                "<b>🚀 MULTIBOT HUB</b>\n\n"
                "Crea tu propio bot hijo sin configurar servidores ni polling.\n\n"
                "✨ Salas públicas/privadas\n"
                "📸 Fotos · 🎬 Vídeos · 📎 Archivos · 🎞 GIF · 🖼 Álbumes\n"
                "🕶️ Distribución anónima\n"
                "📱 Mini App\n\n"
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
        text = (
            f"<b>🤖 @{html.escape(info.username or str(info.bot_id))}</b>\n\n"
            f"🟢 Estado: <b>{html.escape(status)}</b>\n"
            f"🆔 <code>{info.bot_id}</code>\n"
            f"👤 Owner: <code>{info.owner_id}</code>\n"
            f"📦 Cola: <b>{queue}</b>\n"
            f"🔄 Reinicios: <b>{info.restart_count}</b>\n"
            f"❤️ Heartbeat: <code>{html.escape(str(info.last_heartbeat or '—'))}</code>"
        )
        rows = []
        if info.username:
            rows.append([InlineKeyboardButton(text="🤖 Abrir bot", url=f"https://t.me/{info.username}")])
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
                await manager.stop_bot(bot_id)
                result = await manager.delete_bot(bot_id)
                await _safe_callback_answer(callback, result, show_alert=False)
                await callback.message.edit_text("🗑️ Bot eliminado.", reply_markup=menu(int(callback.from_user.id)))
                return
            else:
                raise ValueError("Acción inválida")
            await _safe_callback_answer(callback, result, show_alert=False)
        except Exception as exc:
            await _safe_callback_answer(callback, str(exc)[:180], show_alert=True)
        await show_info(callback, bot_id)

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
