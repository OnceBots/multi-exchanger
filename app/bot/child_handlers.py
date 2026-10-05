from __future__ import annotations

import time
import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo
from urllib.parse import quote, urlencode

from app.services.webapp_auth import make_launch_token

from app.core.enums import MemberRole, RoomVisibility
from app.services.room_service import RoomService




async def _safe_callback_answer(callback: CallbackQuery, *args, **kwargs) -> None:
    """Answer a callback without turning an expired query into a webhook failure."""
    try:
        await callback.answer(*args, **kwargs)
    except Exception as exc:
        message = str(exc).lower()
        if "query is too old" in message or "query id is invalid" in message or "response timeout expired" in message:
            return
        raise
def _webapp_url(ctx, user_id: int) -> str:
    # Match the proven Mini App launch pattern used by aprovebot.py:
    # identify the child/user in the query string and keep a signed token as
    # the authenticated fallback when Telegram does not expose initData.
    token = make_launch_token(
        ctx.settings.webhook_secret,
        ctx.bot_id,
        user_id,
        ctx.settings.webapp_launch_ttl_seconds,
    )
    # Same personalized URL shape used by the proven aprovebot.py Mini App.
    # Keep both user_id and id for compatibility with older clients.
    params = {
        "bot": ctx.bot_username or str(ctx.bot_id),
        "bot_id": str(ctx.bot_id),
        "user_id": str(user_id),
        "id": str(user_id),
        "v": str(int(time.time())),
        "launch": token,
    }
    return f"{ctx.settings.app_base_url}/app?{urlencode(params)}"


async def _configure_user_miniapp_menu(ctx, user_id: int) -> None:
    """Attach a personalized Mini App launcher to this private chat.

    Telegram already provides validated WebApp.initData to Mini Apps. The
    personalized menu URL adds a short-lived signed fallback token for clients
    that expose the Mini App without the raw initData value.
    """
    try:
        url = _webapp_url(ctx, user_id)
        await ctx.bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonWebApp(text="📱 Mini App", web_app=WebAppInfo(url=url)),
        )
        ctx.logger.info("mini_app_user_menu_ready bot_id=%s user_id=%s", ctx.bot_id, user_id)
    except Exception:
        ctx.logger.exception("mini_app_user_menu_failed bot_id=%s user_id=%s", ctx.bot_id, user_id)


def _menu(ctx, user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Crear sala", callback_data="room:create"), InlineKeyboardButton(text="🌎 Explorar", callback_data="room:public")],
        [InlineKeyboardButton(text="🏠 Mis salas", callback_data="room:mine"), InlineKeyboardButton(text="🚪 Unirse", callback_data="room:join")],
        [InlineKeyboardButton(text="👤 Mi perfil", callback_data="profile"), InlineKeyboardButton(text="❓ Ayuda", callback_data="help")],
        [InlineKeyboardButton(text="📱 Abrir Mini App", web_app=WebAppInfo(url=_webapp_url(ctx, user_id)))],
    ]
    if user_id in ctx.settings.admin_ids:
        rows.append([InlineKeyboardButton(text="🛡️ Moderación", callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _room_share_link(ctx, room: dict) -> str | None:
    username = (ctx.bot_username or "").lstrip("@").strip()
    code = str(room.get("invite_code") or "").strip().upper()
    if not username or len(code) != 7:
        return None
    return f"https://t.me/{username}?start=room_{code}"


def _room_keyboard(ctx, room: dict, member: bool, manageable: bool) -> InlineKeyboardMarkup:
    rows = []
    if not member:
        rows.append([InlineKeyboardButton(text="➕ Unirme", callback_data=f"room:join:{room['room_id']}")])
    else:
        rows.append([InlineKeyboardButton(text="📤 Publicar multimedia", callback_data=f"room:use:{room['room_id']}"), InlineKeyboardButton(text="🚪 Salir", callback_data=f"room:leave:{room['room_id']}")])
    share_link = _room_share_link(ctx, room)
    if share_link:
        share_url = f"https://t.me/share/url?url={quote(share_link, safe='')}&text={quote('Únete a esta sala', safe='')}"
        rows.append([InlineKeyboardButton(text="🔗 Compartir enlace", url=share_url)])
    if manageable:
        rows.append([InlineKeyboardButton(text="👥 Miembros", callback_data=f"room:members:{room['room_id']}"), InlineKeyboardButton(text="⚙️ Gestionar", callback_data=f"room:manage:{room['room_id']}")])
    rows.append([InlineKeyboardButton(text="↩️ Menú", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _room_text(room: dict) -> str:
    visibility = "🌎 Pública" if room.get("visibility") == RoomVisibility.PUBLIC.value else "🔒 Privada"
    expiry = room.get("expires_at")
    expiry_text = expiry.isoformat() if expiry else "Sin vencimiento"
    code = str(room.get("invite_code") or room.get("name") or "-------").upper()
    return (
        f"<b>🏠 SALA <code>{html.escape(code)}</code></b>\n\n"
        f"{visibility}\n"
        f"👥 Miembros: <b>{int(room.get('member_count', 0))}/{int(room.get('max_members', 0))}</b>\n"
        f"🔐 Código de acceso: <code>{html.escape(str(room.get('invite_code', '')))}</code>\n"
        f"⏳ Expira: <code>{html.escape(str(expiry_text))}</code>\n\n"
        "Los mensajes multimedia publicados en la sala se redistribuyen sin mostrar al remitente a los demás miembros."
    )


def _room_list(rooms: list[dict], prefix: str = "open") -> InlineKeyboardMarkup:
    rows = []
    for room in rooms:
        label = f"{room.get('member_count', 0)} 👥 · {str(room.get('invite_code') or room.get('name') or 'Sala').upper()}"
        rows.append([InlineKeyboardButton(text=label[:45], callback_data=f"room:{prefix}:{room['room_id']}")])
    rows.append([InlineKeyboardButton(text="↩️ Menú", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _settings_keyboard(data: dict) -> InlineKeyboardMarkup:
    settings = data.get("settings") or RoomService.default_settings()
    labels = {
        "allow_photo": "📷 Fotos",
        "allow_video": "🎬 Vídeos",
        "allow_files": "📎 Archivos",
        "allow_animation": "🎞 GIF",
        "allow_albums": "🖼️ Álbumes",
    }
    rows = []
    for key, label in labels.items():
        rows.append([InlineKeyboardButton(text=f"{'✅' if settings.get(key, True) else '❌'} {label}", callback_data=f"roomcreate:toggle:{key}")])
    rows.append([InlineKeyboardButton(text="Continuar ➜", callback_data="roomcreate:settings_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Sin vencimiento", callback_data="roomcreate:duration:0")],
        [InlineKeyboardButton(text="30 min", callback_data="roomcreate:duration:30"), InlineKeyboardButton(text="1 hora", callback_data="roomcreate:duration:60")],
        [InlineKeyboardButton(text="6 horas", callback_data="roomcreate:duration:360"), InlineKeyboardButton(text="24 horas", callback_data="roomcreate:duration:1440")],
    ])


def build_router(ctx) -> Router:
    router = Router(name=f"child:{ctx.bot_id}")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if message.from_user:
            await ctx.repositories.user.upsert_from_telegram(ctx.bot_id, message.from_user)
        uid = int(message.from_user.id) if message.from_user else 0
        if uid:
            await _configure_user_miniapp_menu(ctx, uid)
        parts = (message.text or "").split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if payload.startswith("room_") and uid:
            code = payload[5:].strip().upper()
            room = await ctx.repositories.room.resolve(ctx.bot_id, code)
            if not room:
                await message.answer("❌ El enlace de la sala no es válido o la sala ya no está disponible.", reply_markup=_menu(ctx, uid))
                return
            ok, reason = await ctx.repositories.room.join(ctx.bot_id, room["room_id"], uid)
            if ok:
                await ctx.repositories.session.set(ctx.bot_id, uid, "", {"active_room_id": room["room_id"]})
                room = await ctx.repositories.room.get(ctx.bot_id, room["room_id"]) or room
                await message.answer(f"✅ {reason}\n\n" + _room_text(room), reply_markup=_room_keyboard(ctx, room, True, bool(int(room.get("owner_id", 0)) == uid)))
            else:
                await message.answer(f"❌ {reason}", reply_markup=_menu(ctx, uid))
            return
        text = (
            f"<b>🎬 @{html.escape(ctx.bot_username or str(ctx.bot_id))}</b>\n\n"
            "Una plataforma de salas para compartir fotos, vídeos, archivos, GIF y álbumes.\n\n"
            "🔐 <b>Privacidad:</b> las publicaciones de las salas se redistribuyen de forma anónima; el servicio puede procesar contenido para funciones de moderación y seguridad."
        )
        await message.answer(text, reply_markup=_menu(ctx, int(message.from_user.id)))

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            "<b>❓ Ayuda</b>\n\n"
            "• Crea o únete a una sala.\n"
            "• Elige la sala activa desde <b>Mis salas</b>.\n"
            "• Envía una foto, vídeo, documento, GIF o álbum.\n"
            "• El contenido se distribuye a los demás miembros sin mostrar tu identidad.\n\n"
            "Los propietarios y administradores pueden gestionar miembros y configuración.",
            reply_markup=_menu(ctx, int(message.from_user.id)),
        )

    @router.callback_query(F.data == "home")
    async def home(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await callback.message.edit_text(
            f"<b>🎬 @{html.escape(ctx.bot_username or str(ctx.bot_id))}</b>\n\nSelecciona una opción:",
            reply_markup=_menu(ctx, int(callback.from_user.id)),
        )

    @router.callback_query(F.data == "help")
    async def help_callback(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await callback.message.edit_text(
            "<b>❓ Cómo funciona</b>\n\n1. Crea o únete a una sala.\n2. Selecciona una sala activa.\n3. Envía multimedia.\n4. La plataforma la distribuye al resto de la sala.\n\nLa identidad del emisor no se muestra a los miembros.",
            reply_markup=_menu(ctx, int(callback.from_user.id)),
        )

    @router.callback_query(F.data == "profile")
    async def profile(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        uid = int(callback.from_user.id)
        await ctx.repositories.user.upsert_from_telegram(ctx.bot_id, callback.from_user)
        rooms = await ctx.repositories.room.list_for_user(ctx.bot_id, uid, 100)
        owned = sum(1 for room in rooms if int(room.get("owner_id", 0)) == uid)
        active = sum(1 for room in rooms if room.get("status") == "ACTIVE")
        await callback.message.edit_text(
            "<b>👤 Mi perfil</b>\n\n"
            f"🆔 <code>{uid}</code>\n"
            f"🏠 Salas: <b>{len(rooms)}</b>\n"
            f"👑 Creadas por mí: <b>{owned}</b>\n"
            f"🟢 Activas: <b>{active}</b>\n\n"
            "Tu identidad no se expone a los demás miembros por defecto.",
            reply_markup=_menu(ctx, uid),
        )

    @router.callback_query(F.data == "room:create")
    async def create_start(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        uid = int(callback.from_user.id)
        await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_visibility", {"settings": RoomService.default_settings()})
        await callback.message.edit_text(
            "<b>➕ Crear sala</b>\n\n"
            "🔐 El sistema generará automáticamente un <b>código aleatorio de 7 caracteres</b>.\n"
            "No se puede elegir nombre ni descripción.\n\n"
            "🌐 Selecciona la visibilidad:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🌎 Pública", callback_data="roomcreate:visibility:PUBLIC"),
                InlineKeyboardButton(text="🔒 Privada", callback_data="roomcreate:visibility:PRIVATE"),
            ],[InlineKeyboardButton(text="✖️ Cancelar", callback_data="roomcreate:cancel")]]),
        )

    @router.callback_query(F.data == "room:public")
    async def public_rooms(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        rooms = await ctx.repositories.room.list_public(ctx.bot_id, 25)
        if not rooms:
            await callback.message.edit_text("<b>🌎 Salas públicas</b>\n\nTodavía no hay salas activas.", reply_markup=_menu(ctx, int(callback.from_user.id)))
            return
        await callback.message.edit_text("<b>🌎 Salas públicas</b>\n\nSelecciona una:", reply_markup=_room_list(rooms, "public"))

    @router.callback_query(F.data == "room:mine")
    async def my_rooms(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        rooms = await ctx.repositories.room.list_for_user(ctx.bot_id, int(callback.from_user.id), 50)
        if not rooms:
            await callback.message.edit_text("<b>🏠 Mis salas</b>\n\nAún no perteneces a ninguna.", reply_markup=_menu(ctx, int(callback.from_user.id)))
            return
        await callback.message.edit_text("<b>🏠 Mis salas</b>\n\nSelecciona una para verla y usarla:", reply_markup=_room_list(rooms, "mine"))

    @router.callback_query(F.data == "room:join")
    async def join_start(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        uid = int(callback.from_user.id)
        await ctx.repositories.session.set(ctx.bot_id, uid, "join_room", {})
        await callback.message.answer("🚪 Envía el ID o código de la sala.")

    async def show_room(callback: CallbackQuery, room_id: str) -> None:
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        if not room:
            await _safe_callback_answer(callback, "Sala no encontrada", show_alert=True)
            return
        uid = int(callback.from_user.id)
        member = await ctx.repositories.room.member(ctx.bot_id, room_id, uid)
        manageable = bool(member and member.get("role") in {MemberRole.OWNER.value, MemberRole.ADMIN.value})
        await callback.message.edit_text(_room_text(room), reply_markup=_room_keyboard(ctx, room, bool(member), manageable))

    @router.callback_query(F.data.startswith("room:public:"))
    async def public_open(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await show_room(callback, callback.data.split(":", 2)[2])

    @router.callback_query(F.data.startswith("room:mine:"))
    async def mine_open(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        await show_room(callback, callback.data.split(":", 2)[2])

    @router.callback_query(F.data.startswith("room:join:"))
    async def join_existing(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        ok, reason, room = await ctx.services.rooms.join(ctx.bot_id, int(callback.from_user.id), room_id)
        if ok and room:
            await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "", {"active_room_id": room_id})
        await _safe_callback_answer(callback, reason, show_alert=not ok)
        if room:
            await show_room(callback, room_id)

    @router.callback_query(F.data.startswith("room:use:"))
    async def use_room(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        member = await ctx.repositories.room.member(ctx.bot_id, room_id, int(callback.from_user.id))
        if not member:
            await _safe_callback_answer(callback, "No eres miembro.", show_alert=True)
            return
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "", {"active_room_id": room_id})
        await _safe_callback_answer(callback, "✅ Sala activa")
        await callback.message.answer("📡 Sala activa. Envía ahora una foto, vídeo, documento, GIF o álbum.")

    @router.callback_query(F.data.startswith("room:leave:"))
    async def leave_room(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        ok, reason = await ctx.services.rooms.leave(ctx.bot_id, room_id, int(callback.from_user.id))
        await _safe_callback_answer(callback, reason, show_alert=not ok)
        if ok:
            await ctx.repositories.session.clear(ctx.bot_id, int(callback.from_user.id))
            await callback.message.edit_text("🚪 Has salido de la sala.", reply_markup=_menu(ctx, int(callback.from_user.id)))

    @router.callback_query(F.data.startswith("room:members:"))
    async def room_members(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.rooms.can_manage(ctx.bot_id, room_id, int(callback.from_user.id)):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        members = await ctx.repositories.room.members_for_room(ctx.bot_id, room_id)
        rows = []
        text = "<b>👥 Miembros</b>\n\n"
        for member in members[:25]:
            role = member.get("role")
            icon = "👑" if role == MemberRole.OWNER.value else "🛡️" if role == MemberRole.ADMIN.value else "•"
            text += f"{icon} <code>{member['user_id']}</code> · {role}\n"
            if role != MemberRole.OWNER.value:
                rows.append([InlineKeyboardButton(text=f"🗑 Expulsar {member['user_id']}", callback_data=f"room:kick:{room_id}:{member['user_id']}")])
        rows.append([InlineKeyboardButton(text="↩️ Sala", callback_data=f"room:public:{room_id}")])
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("room:kick:"))
    async def kick_member(callback: CallbackQuery) -> None:
        _, _, room_id, target = callback.data.split(":", 3)
        if not await ctx.services.rooms.can_manage(ctx.bot_id, room_id, int(callback.from_user.id)):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        await ctx.repositories.room.remove_member(ctx.bot_id, room_id, int(target))
        await ctx.repositories.audit.log(ctx.bot_id, int(callback.from_user.id), "ROOM_MEMBER_KICK", target=target, details={"room_id": room_id})
        await _safe_callback_answer(callback, "Miembro expulsado")
        members = await ctx.repositories.room.members_for_room(ctx.bot_id, room_id)
        text = "<b>👥 Miembros</b>\n\n"
        rows = []
        for member in members[:25]:
            role = member.get("role")
            icon = "👑" if role == MemberRole.OWNER.value else "🛡️" if role == MemberRole.ADMIN.value else "•"
            text += f"{icon} <code>{member['user_id']}</code> · {role}\n"
            if role != MemberRole.OWNER.value:
                rows.append([InlineKeyboardButton(text=f"🗑 Expulsar {member['user_id']}", callback_data=f"room:kick:{room_id}:{member['user_id']}")])
        rows.append([InlineKeyboardButton(text="↩️ Sala", callback_data=f"room:public:{room_id}")])
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("room:manage:"))
    async def room_manage(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.rooms.can_manage(ctx.bot_id, room_id, int(callback.from_user.id)):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        await callback.message.edit_text(
            _room_text(room or {}) + "\n\n<b>⚙️ Gestión</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏳ Sin vencimiento", callback_data=f"roomedit:never:{room_id}")],
                [InlineKeyboardButton(text="↩️ Sala", callback_data=f"room:public:{room_id}")],
            ]),
        )

    @router.callback_query(F.data.startswith("roomedit:never:"))
    async def edit_never(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.rooms.can_manage(ctx.bot_id, room_id, int(callback.from_user.id)):
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        await ctx.repositories.room.update(ctx.bot_id, room_id, expires_at=None)
        await _safe_callback_answer(callback, "Expiración desactivada")
        await show_room(callback, room_id)

    @router.callback_query(F.data == "roomcreate:settings_done")
    async def settings_done(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        session = await ctx.repositories.session.get(ctx.bot_id, uid)
        data = (session or {}).get("data", {})
        await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_duration", data)
        await _safe_callback_answer(callback)
        await callback.message.edit_text("⏳ ¿Cuánto tiempo estará activa la sala?", reply_markup=_duration_keyboard())

    @router.callback_query(F.data.startswith("roomcreate:toggle:"))
    async def settings_toggle(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        key = callback.data.split(":", 2)[2]
        session = await ctx.repositories.session.get(ctx.bot_id, uid)
        data = dict((session or {}).get("data") or {})
        settings = dict(data.get("settings") or RoomService.default_settings())
        settings[key] = not bool(settings.get(key, True))
        data["settings"] = settings
        await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_settings", data)
        await callback.message.edit_reply_markup(reply_markup=_settings_keyboard(data))
        await _safe_callback_answer(callback, "Actualizado")

    @router.callback_query(F.data.startswith("roomcreate:visibility:"))
    async def create_visibility(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        value = callback.data.split(":", 2)[2]
        session = await ctx.repositories.session.get(ctx.bot_id, uid)
        data = dict((session or {}).get("data") or {})
        data["visibility"] = value
        await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_max", data)
        await _safe_callback_answer(callback)
        await callback.message.answer("👥 ¿Capacidad máxima? Escribe un número entre 2 y 10000.")

    @router.callback_query(F.data.startswith("roomcreate:duration:"))
    async def create_duration(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        minutes = int(callback.data.split(":")[-1])
        session = await ctx.repositories.session.get(ctx.bot_id, uid)
        data = dict((session or {}).get("data") or {})
        data["duration_minutes"] = minutes
        await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_confirm", data)
        summary = (
            "<b>✅ Revisar sala</b>\n\n"
            "🔐 Código: <b>se generará al crear</b>\n"
            f"🌐 {'Pública' if data.get('visibility') == 'PUBLIC' else 'Privada'}\n"
            f"👥 {data.get('max_members', 0)} miembros\n"
            f"⏳ {minutes if minutes else 'sin vencimiento'}"
        )
        await _safe_callback_answer(callback)
        await callback.message.edit_text(summary, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Crear", callback_data="roomcreate:confirm"), InlineKeyboardButton(text="✖️ Cancelar", callback_data="roomcreate:cancel")]]))

    @router.callback_query(F.data == "roomcreate:confirm")
    async def create_confirm(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        session = await ctx.repositories.session.get(ctx.bot_id, uid)
        data = dict((session or {}).get("data") or {})
        if not data.get("visibility"):
            await _safe_callback_answer(callback, "La sesión expiró", show_alert=True)
            return
        room = await ctx.services.rooms.create_room(ctx.bot_id, uid, data["visibility"], int(data.get("max_members", 2)), data.get("settings") or RoomService.default_settings(), int(data.get("duration_minutes") or 0))
        await ctx.repositories.session.clear(ctx.bot_id, uid)
        await ctx.repositories.session.set(ctx.bot_id, uid, "", {"active_room_id": room["room_id"]})
        await ctx.repositories.audit.log(ctx.bot_id, uid, "ROOM_CREATED", target=room["room_id"], details={"visibility": room["visibility"]})
        await _safe_callback_answer(callback, "Sala creada")
        await callback.message.edit_text(_room_text(room), reply_markup=_room_keyboard(ctx, room, True, True))

    @router.callback_query(F.data == "roomcreate:cancel")
    async def create_cancel(callback: CallbackQuery) -> None:
        await ctx.repositories.session.clear(ctx.bot_id, int(callback.from_user.id))
        await _safe_callback_answer(callback, "Cancelado")
        await callback.message.edit_text("❎ Creación cancelada.", reply_markup=_menu(ctx, int(callback.from_user.id)))

    @router.callback_query(F.data == "admin:panel")
    async def admin_panel(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid not in ctx.settings.admin_ids:
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        enabled = await ctx.repositories.admin_feed.is_enabled(ctx.bot_id, uid)
        status = "🟢 ACTIVO" if enabled else ("🟢 ACTIVO (predeterminado)" if ctx.settings.admin_feed_enabled_by_default else "⚪ INACTIVO")
        await callback.message.edit_text(
            "<b>🛡️ Moderación</b>\n\n"
            f"Feed de moderación: <b>{status}</b>\n\n"
            "El feed permite revisar el contenido recibido por el bot para tareas de seguridad/moderación.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔛 Activar", callback_data="adminfeed:on"), InlineKeyboardButton(text="⛔ Desactivar", callback_data="adminfeed:off")],
                [InlineKeyboardButton(text="↩️ Menú", callback_data="home")],
            ]),
        )

    @router.callback_query(F.data == "adminfeed:on")
    async def admin_feed_on(callback: CallbackQuery) -> None:
        if int(callback.from_user.id) not in ctx.settings.admin_ids:
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        await ctx.repositories.admin_feed.set_enabled(ctx.bot_id, int(callback.from_user.id), True)
        await _safe_callback_answer(callback, "Feed activado")
        await admin_panel(callback)

    @router.callback_query(F.data == "adminfeed:off")
    async def admin_feed_off(callback: CallbackQuery) -> None:
        if int(callback.from_user.id) not in ctx.settings.admin_ids:
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        await ctx.repositories.admin_feed.set_enabled(ctx.bot_id, int(callback.from_user.id), False)
        await _safe_callback_answer(callback, "Feed desactivado")
        await admin_panel(callback)

    @router.message()
    async def messages(message: Message) -> None:
        if not message.from_user:
            return
        uid = int(message.from_user.id)
        await ctx.repositories.user.upsert_from_telegram(ctx.bot_id, message.from_user)
        if message.text and not message.text.startswith("/"):
            session = await ctx.repositories.session.get(ctx.bot_id, uid)
            step = (session or {}).get("step")
            data = dict((session or {}).get("data") or {})
            if step == "join_room":
                await ctx.repositories.session.clear(ctx.bot_id, uid)
                ok, reason, room = await ctx.services.rooms.join(ctx.bot_id, uid, message.text.strip())
                if ok and room:
                    await ctx.repositories.session.set(ctx.bot_id, uid, "", {"active_room_id": room["room_id"]})
                    await message.answer("✅ " + reason, reply_markup=_room_keyboard(ctx, room, True, False))
                else:
                    await message.answer("❌ " + reason, reply_markup=_menu(ctx, uid))
                return
            if step == "room_create_max":
                if not message.text.strip().isdigit():
                    await message.answer("Escribe un número válido.")
                    return
                max_members = int(message.text.strip())
                if not 2 <= max_members <= 10000:
                    await message.answer("La capacidad debe estar entre 2 y 10000.")
                    return
                data["max_members"] = max_members
                await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_settings", data)
                await message.answer("🎛 Permisos multimedia:", reply_markup=_settings_keyboard(data))
                return
        if message.photo or message.video or message.document or message.animation:
            await ctx.services.media.handle_message(message)
            # Direct media in a private chat is also eligible for the moderation feed.
            if message.chat.type == "private" and int(message.from_user.id) in ctx.settings.admin_ids:
                return
            return
        if message.text and not message.text.startswith("/"):
            await message.answer("Usa el menú para crear o seleccionar una sala.", reply_markup=_menu(ctx, uid))

    return router
