from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from urllib.parse import quote
from datetime import timedelta


from app.core.enums import MemberRole, RoomVisibility
from app.services.room_service import InsufficientReferralCredits, RoomService
from app.services.bot_lifecycle import botfather_instructions, schedule_label




async def _safe_callback_answer(callback: CallbackQuery, *args, **kwargs) -> None:
    """Answer a callback without turning an expired query into a webhook failure."""
    try:
        await callback.answer(*args, **kwargs)
    except Exception as exc:
        message = str(exc).lower()
        if "query is too old" in message or "query id is invalid" in message or "response timeout expired" in message:
            return
        raise
def _menu(ctx, user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Crear sala", callback_data="room:create"), InlineKeyboardButton(text="🌎 Explorar", callback_data="room:public")],
        [InlineKeyboardButton(text="🏠 Mis salas", callback_data="room:mine"), InlineKeyboardButton(text="🚪 Unirse", callback_data="room:join")],
        [InlineKeyboardButton(text="👤 Mi perfil", callback_data="profile"), InlineKeyboardButton(text="🎁 Mis referidos", callback_data="referrals:mine")],
        [InlineKeyboardButton(text="❓ Ayuda", callback_data="help")],
    ]
    if user_id == ctx.owner_id:
        rows.append([InlineKeyboardButton(text="⏱️ Temporizador", callback_data="bot:lifecycle"), InlineKeyboardButton(text="🗑️ Eliminar bot", callback_data="bot:deletehelp")])
    if user_id in ctx.settings.admin_ids:
        rows.append([InlineKeyboardButton(text="🛡️ Moderación", callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _room_share_link(ctx, room: dict) -> str | None:
    username = (ctx.bot_username or "").lstrip("@").strip()
    code = str(room.get("invite_code") or "").strip().upper()
    if not username or len(code) != 7:
        return None
    return f"https://t.me/{username}?start=room_{code}"


def _referral_link(ctx, user_id: int) -> str | None:
    username = (ctx.bot_username or "").lstrip("@").strip()
    if not username or int(user_id) <= 0:
        return None
    return f"https://t.me/{username}?start=ref_{int(user_id)}"


def _referral_keyboard(ctx, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    link = _referral_link(ctx, user_id)
    if link:
        share_url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote('Únete al bot y ayúdame a desbloquear salas', safe='')}"
        rows.append([InlineKeyboardButton(text="🔗 Compartir mi enlace", url=share_url)])
    rows.append([InlineKeyboardButton(text="↩️ Menú", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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



async def _current_media_policy(ctx) -> dict:
    """Load the durable per-child-bot media policy controlled by ADMIN_IDS."""
    doc = await ctx.repositories.bots.get(ctx.bot_id)
    config = (doc or {}).get("config") or {}
    return RoomService.normalize_media_policy(config.get("room_media_policy"))

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
        f"🔑 Contraseña: <b>{'Sí' if room.get('password_protected') else 'No'}</b>\n"
        f"⏳ Expira: <code>{html.escape(str(expiry_text))}</code>\n\n"
        "📦 Cada miembro tiene su propio contador y su ZIP privado. Para habilitar la entrega, ese usuario debe reunir 10 vídeos únicos guardados durante una ventana de 60 minutos. El paquete se cierra al alcanzar 1,8 GiB o tras 60 segundos sin nuevos archivos, cuando ya cumpla el mínimo. El ZIP final queda archivado en el canal configurado y se envía al usuario que aportó el contenido y a los administradores."
    )


def _room_list(rooms: list[dict], prefix: str = "open") -> InlineKeyboardMarkup:
    rows = []
    for room in rooms:
        label = f"{room.get('member_count', 0)} 👥 · {str(room.get('invite_code') or room.get('name') or 'Sala').upper()}"
        rows.append([InlineKeyboardButton(text=label[:45], callback_data=f"room:{prefix}:{room['room_id']}")])
    rows.append([InlineKeyboardButton(text="↩️ Menú", callback_data="home")])
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
        uid = int(message.from_user.id) if message.from_user else 0
        is_new_user = False
        if message.from_user:
            previous_user = await ctx.repositories.user.get(ctx.bot_id, uid)
            is_new_user = previous_user is None
            await ctx.repositories.user.upsert_from_telegram(ctx.bot_id, message.from_user)
        parts = (message.text or "").split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if payload.startswith("room_") and uid:
            code = payload[5:].strip().upper()
            room = await ctx.repositories.room.resolve(ctx.bot_id, code)
            if not room:
                await message.answer("❌ El enlace de la sala no es válido o la sala ya no está disponible.", reply_markup=_menu(ctx, uid))
                return
            if room.get("visibility") == RoomVisibility.PRIVATE.value and room.get("password_protected"):
                await ctx.repositories.session.set(ctx.bot_id, uid, "join_room_password", {"room_id": room["room_id"]})
                await message.answer("🔒 Esta sala es privada. Envía la contraseña para continuar.")
                return
            ok, reason = await ctx.repositories.room.join(ctx.bot_id, room["room_id"], uid)
            if ok:
                await ctx.repositories.session.set(ctx.bot_id, uid, "", {"active_room_id": room["room_id"]})
                room = await ctx.repositories.room.get(ctx.bot_id, room["room_id"]) or room
                await message.answer(f"✅ {reason}\n\n" + _room_text(room), reply_markup=_room_keyboard(ctx, room, True, bool(int(room.get("owner_id", 0)) == uid)))
            else:
                await message.answer(f"❌ {reason}", reply_markup=_menu(ctx, uid))
            return
        referral_notice = ""
        if is_new_user and payload.startswith("ref_") and uid:
            referrer_raw = payload[4:].strip()
            if referrer_raw.isdigit():
                added = await ctx.repositories.referrals.register_referral(ctx.bot_id, int(referrer_raw), uid)
                if added:
                    referral_notice = "✅ Tu registro quedó asociado a un enlace de invitación. ¡Gracias por unirte!"
        text = (
            f"<b>🎬 @{html.escape(ctx.bot_username or str(ctx.bot_id))}</b>\n\n"
            "Una plataforma de salas que recopila multimedia y la agrupa en ZIP protegidos con contraseña. Los administradores pueden habilitar vídeos, fotos, archivos, GIF, audio, notas de voz, stickers y álbumes.\n\n"
            "🎁 <b>Creación de salas:</b> cada sala cuesta 10 referidos válidos. Comparte tu enlace personal desde <b>🎁 Mis referidos</b>; cuenta un usuario nuevo cuando inicia este bot desde tu enlace. Las cuentas incluidas en ADMIN_IDS no gastan referidos.\n\n"
            "📦 <b>Paquetes individuales:</b> cada usuario tiene su propio contador y su propio ZIP. Para habilitar la entrega debe reunir 10 vídeos únicos guardados durante una ventana de 60 minutos. El ZIP se cierra al alcanzar 1,8 GiB o tras 60 segundos sin nuevos archivos, siempre que se cumpla el mínimo.\n\n"
            "🗂️ Cada ZIP final queda archivado en el canal configurado y se envía en privado a quien aportó esos archivos y a los administradores; no se mezcla ni se entrega a los demás miembros.\n\n"
            "🔐 Los originales solo se borran después de descargarse y guardarse correctamente en Vultr."
        )
        if referral_notice:
            text = referral_notice + "\n\n" + text
        await message.answer(text, reply_markup=_menu(ctx, int(message.from_user.id)))
        if uid == ctx.owner_id:
            doc = await ctx.repositories.bots.get(ctx.bot_id)
            cfg = (doc or {}).get("config") or {}
            if not cfg.get("owner_onboarding_sent"):
                onboarding = (
                    "<b>✅ BOT PROPIO ACTIVO</b>\n\n"
                    "Este es tu bot hijo. Puedes crear salas, compartir enlaces y administrar todo desde este chat.\n\n"
                    "⏱️ El temporizador está en modo manual por defecto.\n"
                    "🗑️ Si decides eliminarlo definitivamente, usa el botón <b>🗑️ Eliminar bot</b> y sigue los pasos de @BotFather."
                )
                await message.answer(onboarding)
                await ctx.repositories.bots.update_config(ctx.bot_id, {"owner_onboarding_sent": True})

    @router.callback_query(F.data == "bot:lifecycle")
    async def bot_lifecycle(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid != ctx.owner_id:
            await _safe_callback_answer(callback, "Solo el propietario puede cambiar el temporizador.", show_alert=True)
            return
        rows = [
            [InlineKeyboardButton(text="5 min", callback_data="bot:lifecycle:5"), InlineKeyboardButton(text="15 min", callback_data="bot:lifecycle:15")],
            [InlineKeyboardButton(text="30 min", callback_data="bot:lifecycle:30"), InlineKeyboardButton(text="1 hora", callback_data="bot:lifecycle:60")],
            [InlineKeyboardButton(text="6 horas", callback_data="bot:lifecycle:360"), InlineKeyboardButton(text="12 horas", callback_data="bot:lifecycle:720")],
            [InlineKeyboardButton(text="24 horas", callback_data="bot:lifecycle:1440"), InlineKeyboardButton(text="48 horas", callback_data="bot:lifecycle:2880")],
            [InlineKeyboardButton(text="72 horas", callback_data="bot:lifecycle:4320"), InlineKeyboardButton(text="♾️ Manual", callback_data="bot:lifecycle:0")],
            [InlineKeyboardButton(text="↩️ Menú", callback_data="home")],
        ]
        await _safe_callback_answer(callback)
        await callback.message.edit_text("<b>⏱️ Temporizador del bot</b>\n\nAl vencer, el bot se detendrá en la plataforma y recibirás los pasos para eliminarlo definitivamente desde @BotFather.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("bot:lifecycle:"))
    async def bot_lifecycle_set(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid != ctx.owner_id:
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        minutes = int(callback.data.split(":")[-1])
        if minutes == 0:
            await ctx.repositories.bots.clear_lifecycle(ctx.bot_id)
            text = "⏱️ Temporizador desactivado. El bot queda en modo manual."
        else:
            from app.core.datetime import utcnow
            now = utcnow()
            delete_at = now + timedelta(minutes=minutes)
            await ctx.repositories.bots.set_lifecycle(ctx.bot_id, {"mode": "TIMER", "delete_at": delete_at, "scheduled_at": now, "scheduled_by": uid, "label": schedule_label(minutes)})
            text = f"⏱️ Temporizador configurado: <b>{schedule_label(minutes)}</b>.\nVence: <code>{html.escape(delete_at.isoformat())}</code>"
        await _safe_callback_answer(callback, "Actualizado")
        await callback.message.edit_text(text, reply_markup=_menu(ctx, uid))

    @router.callback_query(F.data == "bot:deletehelp")
    async def bot_delete_help(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid != ctx.owner_id:
            await _safe_callback_answer(callback, "Solo el propietario puede eliminar este bot.", show_alert=True)
            return
        text, markup = botfather_instructions(ctx.bot_username, reason="manual")
        await _safe_callback_answer(callback)
        await callback.message.answer(text, reply_markup=markup)

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            "<b>❓ Ayuda</b>\n\n"
            "• Crea o únete a una sala.\n"
            "• Elige la sala activa desde <b>Mis salas</b>.\n"
            "• Envía contenido multimedia permitido. Otros formatos los habilitan los administradores del bot.\n"
            "• Los archivos se descargan a Vultr; al confirmarse la copia local, el mensaje original se borra.\n"
            "• Cada usuario tiene un contador independiente: necesita 10 vídeos únicos guardados durante los últimos 60 minutos para habilitar su ZIP.\n"
            "• El ZIP final se publica en el canal configurado y se copia en privado al usuario que aportó los archivos y a los administradores. Los ZIP personales no se comparten con los demás miembros.\n"
            "• Los originales solo se borran tras verificarse la copia local en Vultr.\n\n"
            "El paquete se cierra al alcanzar aproximadamente 1,8 GiB o tras 60 segundos sin nuevos archivos, una vez cumplida la meta. Usa 7-Zip o WinRAR para extraerlo.",
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
            "<b>❓ Cómo funciona</b>\n\n1. Crea o únete a una sala.\n2. Selecciona la sala activa.\n3. Envía los archivos multimedia permitidos.\n4. El bot confirma la descarga en Vultr y después borra el mensaje original.\n5. Tu contador personal necesita 10 vídeos únicos guardados en una ventana móvil de 60 minutos. Fotos u otros formatos permitidos pueden acompañar tu ZIP, pero no cuentan como vídeos.\n6. Al cumplir la meta, el ZIP cifrado con AES-256 se publica en el canal configurado y se envía en privado a ti y a los administradores.\n\nEl ZIP se cierra al alcanzar aproximadamente 1,8 GiB o tras 60 segundos sin nuevos archivos, siempre que se haya cumplido la meta. El progreso se muestra en un mensaje actualizable. Por defecto solo se permiten vídeos; ADMIN_IDS controla los demás formatos desde 🛡️ Moderación.",
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

    @router.callback_query(F.data == "referrals:mine")
    async def referrals_mine(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        uid = int(callback.from_user.id)
        status = await ctx.services.rooms.get_creation_status(ctx.bot_id, uid)
        link = _referral_link(ctx, uid)
        if status.get("is_admin"):
            access = "🛡️ Tu cuenta está en ADMIN_IDS: puedes crear salas sin gastar referidos."
        else:
            access = (
                f"🏠 Crear una sala cuesta <b>10 referidos válidos</b>.\n"
                f"🎟️ Créditos disponibles: <b>{int(status.get('credits_available', 0))}</b>.\n"
                f"📌 Cada usuario nuevo que inicie el bot desde tu enlace cuenta una sola vez."
            )
        text = (
            "<b>🎁 MIS REFERIDOS</b>\n\n"
            f"👥 Usuarios nuevos invitados: <b>{int(status.get('valid_referrals', 0))}</b>\n"
            f"🏠 Salas pagadas con referidos: <b>{int(status.get('rooms_paid', 0))}</b>\n"
            f"📉 Créditos gastados: <b>{int(status.get('credits_spent', 0))}</b>\n\n"
            f"{access}"
        )
        if link:
            text += f"\n\n🔗 Tu enlace personal:\n<code>{html.escape(link)}</code>"
        await callback.message.edit_text(text, reply_markup=_referral_keyboard(ctx, uid))

    @router.callback_query(F.data == "room:create")
    async def create_start(callback: CallbackQuery) -> None:
        await _safe_callback_answer(callback)
        uid = int(callback.from_user.id)
        quota = await ctx.services.rooms.get_creation_status(ctx.bot_id, uid)
        if not quota.get("is_admin") and int(quota.get("credits_available", 0)) < 10:
            missing = 10 - int(quota.get("credits_available", 0))
            text = (
                "<b>🔒 Sala bloqueada por referidos</b>\n\n"
                "Cada sala nueva cuesta 10 referidos válidos. Solo cuentan usuarios nuevos que inicien este bot desde tu enlace personal; cada persona cuenta una vez.\n\n"
                f"👥 Referidos válidos: <b>{int(quota.get('valid_referrals', 0))}</b>\n"
                f"🎟️ Créditos disponibles: <b>{int(quota.get('credits_available', 0))}</b>\n"
                f"📣 Te faltan <b>{missing}</b> créditos para crear una sala."
            )
            link = _referral_link(ctx, uid)
            if link:
                text += f"\n\n🔗 Tu enlace:\n<code>{html.escape(link)}</code>"
            await callback.message.edit_text(text, reply_markup=_referral_keyboard(ctx, uid))
            return
        policy = await _current_media_policy(ctx)
        await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_visibility", {"settings": dict(policy)})
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
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        if room and room.get("visibility") == RoomVisibility.PRIVATE.value and room.get("password_protected"):
            await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "join_room_password", {"room_id": room_id})
            await _safe_callback_answer(callback)
            await callback.message.answer("🔒 Esta sala privada requiere contraseña. Envíala ahora.")
            return
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
        await callback.message.answer("📡 Sala activa. Envía ahora un vídeo. Otros formatos dependen de los permisos globales del bot.")

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

    @router.callback_query(F.data.startswith("roomcreate:visibility:"))
    async def create_visibility(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        value = callback.data.split(":", 2)[2]
        session = await ctx.repositories.session.get(ctx.bot_id, uid)
        data = dict((session or {}).get("data") or {})
        data["visibility"] = value
        if value == RoomVisibility.PRIVATE.value:
            await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_password", data)
            await _safe_callback_answer(callback)
            await callback.message.answer("🔒 Escribe una contraseña de 4 a 64 caracteres para la sala privada.")
        else:
            data.pop("password", None)
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
        try:
            room = await ctx.services.rooms.create_room(ctx.bot_id, uid, data["visibility"], int(data.get("max_members", 2)), data.get("settings") or RoomService.default_settings(), int(data.get("duration_minutes") or 0), data.get("password"))
        except InsufficientReferralCredits as exc:
            await _safe_callback_answer(callback, str(exc), show_alert=True)
            quota = await ctx.services.rooms.get_creation_status(ctx.bot_id, uid)
            missing = max(0, 10 - int(quota.get("credits_available", 0)))
            text = (
                "<b>🔒 No se puede crear la sala todavía</b>\n\n"
                f"Tienes {int(quota.get('credits_available', 0))} créditos y necesitas 10. Te faltan {missing}.\n"
                "Comparte tu enlace personal y consigue nuevos usuarios que inicien el bot desde él."
            )
            link = _referral_link(ctx, uid)
            if link:
                text += f"\n\n🔗 <code>{html.escape(link)}</code>"
            await callback.message.edit_text(text, reply_markup=_referral_keyboard(ctx, uid))
            return
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

    @router.callback_query(F.data.startswith("pack:status:"))
    async def package_status(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        package_id = callback.data.rsplit(":", 1)[-1]
        package = await ctx.repositories.packages.get_package(ctx.bot_id, package_id)
        if not package:
            await _safe_callback_answer(callback, "Este paquete ya no está disponible.", show_alert=True)
            return
        owner_id = package.get("sender_id")
        if uid not in ctx.settings.admin_ids:
            if owner_id is not None:
                authorized = uid == int(owner_id)
            else:
                # Legacy room-wide packages keep the old member-based access rule.
                authorized = await ctx.repositories.room.is_member(ctx.bot_id, str(package.get("room_id", "")), uid)
            if not authorized:
                await _safe_callback_answer(callback, "No estás autorizado para ver este paquete.", show_alert=True)
                return
        text, _ = await ctx.services.packages.status_text(package_id, uid)
        await _safe_callback_answer(callback, "Estado actualizado")
        try:
            await callback.message.edit_text(text, reply_markup=ctx.services.packages._status_markup(package_id, uid))
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                raise

    @router.callback_query(F.data.startswith("pack:close:"))
    async def package_close(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid not in ctx.settings.admin_ids:
            await _safe_callback_answer(callback, "Solo ADMIN_IDS puede cerrar un paquete manualmente.", show_alert=True)
            return
        package_id = callback.data.rsplit(":", 1)[-1]
        closed = await ctx.services.packages.close_now(package_id)
        if not closed:
            await _safe_callback_answer(callback, "El paquete ya está cerrado o no existe.", show_alert=True)
            return
        await _safe_callback_answer(callback, "Se cerrará cuando terminen las descargas pendientes.")
        text, _ = await ctx.services.packages.status_text(package_id, uid)
        try:
            await callback.message.edit_text(text, reply_markup=ctx.services.packages._status_markup(package_id, uid))
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                raise

    @router.callback_query(F.data == "admin:panel")
    async def admin_panel(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid not in ctx.settings.admin_ids:
            await _safe_callback_answer(callback, "No autorizado", show_alert=True)
            return
        enabled = await ctx.repositories.admin_feed.is_enabled(ctx.bot_id, uid)
        status = "🟢 ACTIVO" if enabled else ("🟢 ACTIVO (predeterminado)" if ctx.settings.admin_feed_enabled_by_default else "⚪ INACTIVO")
        policy = await _current_media_policy(ctx)
        labels = {
            "allow_video": "🎬 Vídeos",
            "allow_photo": "📷 Fotos",
            "allow_files": "📎 Archivos",
            "allow_animation": "🎞 GIF",
            "allow_albums": "🖼️ Álbumes",
            "allow_audio": "🎵 Audio",
            "allow_voice": "🎙️ Notas de voz",
            "allow_video_note": "⭕ Vídeos circulares",
            "allow_sticker": "🎭 Stickers",
        }
        media_rows = [
            [InlineKeyboardButton(
                text=f"{'✅' if policy[key] else '❌'} {label}",
                callback_data=f"adminmedia:toggle:{key}",
            )]
            for key, label in labels.items()
        ]
        await callback.message.edit_text(
            "<b>🛡️ Administración del bot</b>\n\n"
            f"Feed de moderación: <b>{status}</b>\n\n"
            "<b>Formatos permitidos en TODAS las salas</b>\n"
            "Por defecto solo se permiten vídeos. Estos interruptores solo los pueden usar los IDs de ADMIN_IDS. "
            "Los cambios se aplican también a salas existentes.\n\n"
            "La multimedia de las salas se envía como ZIP cifrado; los administradores reciben el mismo paquete. Los archivos enviados directamente fuera de una sala siguen el flujo de moderación existente.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔛 Activar feed", callback_data="adminfeed:on"), InlineKeyboardButton(text="⛔ Desactivar feed", callback_data="adminfeed:off")],
                *media_rows,
                [InlineKeyboardButton(text="↩️ Menú", callback_data="home")],
            ]),
        )

    @router.callback_query(F.data.startswith("adminmedia:toggle:"))
    async def admin_media_toggle(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid not in ctx.settings.admin_ids:
            await _safe_callback_answer(callback, "Solo ADMIN_IDS puede cambiar los permisos multimedia.", show_alert=True)
            return
        key = callback.data.rsplit(":", 1)[-1]
        if key not in RoomService.MEDIA_POLICY_KEYS:
            await _safe_callback_answer(callback, "Opción inválida", show_alert=True)
            return
        policy = await _current_media_policy(ctx)
        policy[key] = not policy[key]
        # Prevent disabling videos as the sole medium unless admin intentionally
        # disables them; that would leave rooms with no publishable media.
        await ctx.repositories.bots.update_config(ctx.bot_id, {"room_media_policy": policy})
        await ctx.repositories.audit.log(
            ctx.bot_id, uid, "ROOM_MEDIA_POLICY_UPDATED", target=key,
            details={"enabled": policy[key], "policy": policy},
        )
        await _safe_callback_answer(callback, "Permisos multimedia actualizados")
        await admin_panel(callback)

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
                ref = message.text.strip()
                room = await ctx.repositories.room.resolve(ctx.bot_id, ref)
                if room and room.get("visibility") == RoomVisibility.PRIVATE.value and room.get("password_protected"):
                    await ctx.repositories.session.set(ctx.bot_id, uid, "join_room_password", {"room_id": room["room_id"]})
                    await message.answer("🔒 Esta sala privada requiere contraseña. Envíala ahora.")
                    return
                await ctx.repositories.session.clear(ctx.bot_id, uid)
                ok, reason, room = await ctx.services.rooms.join(ctx.bot_id, uid, ref)
                if ok and room:
                    await ctx.repositories.session.set(ctx.bot_id, uid, "", {"active_room_id": room["room_id"]})
                    await message.answer("✅ " + reason, reply_markup=_room_keyboard(ctx, room, True, False))
                else:
                    await message.answer("❌ " + reason, reply_markup=_menu(ctx, uid))
                return
            if step == "join_room_password":
                room_id = str(data.get("room_id") or "")
                password = message.text.strip()
                await ctx.repositories.session.clear(ctx.bot_id, uid)
                ok, reason, room = await ctx.services.rooms.join(ctx.bot_id, uid, room_id, password=password)
                if ok and room:
                    await ctx.repositories.session.set(ctx.bot_id, uid, "", {"active_room_id": room_id})
                    await message.answer("✅ " + reason + "\n\n" + _room_text(room), reply_markup=_room_keyboard(ctx, room, True, int(room.get("owner_id", 0)) == uid))
                else:
                    await message.answer("❌ " + reason, reply_markup=_menu(ctx, uid))
                return
            if step == "room_create_password":
                password = message.text.strip()
                if not 4 <= len(password) <= 64:
                    await message.answer("La contraseña debe tener entre 4 y 64 caracteres.")
                    return
                data["password"] = password
                await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_max", data)
                await message.answer("👥 ¿Capacidad máxima? Escribe un número entre 2 y 10000.")
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
                # Multimedia permissions are controlled globally from the Admin
                # panel, never by regular room creators.
                await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_duration", data)
                await message.answer("⏳ ¿Cuánto tiempo estará activa la sala?", reply_markup=_duration_keyboard())
                return
        if any((message.photo, message.video, message.document, message.animation, message.audio, message.voice, message.video_note, message.sticker)):
            await ctx.services.media.handle_message(message)
            # Direct media in a private chat is also eligible for the moderation feed.
            if message.chat.type == "private" and int(message.from_user.id) in ctx.settings.admin_ids:
                return
            return
        if message.text and not message.text.startswith("/"):
            await message.answer("Usa el menú para crear o seleccionar una sala.", reply_markup=_menu(ctx, uid))

    return router
