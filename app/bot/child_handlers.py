from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


def _duration_text(minutes: int | None) -> str:
    if not minutes:
        return "sin expiración"
    if minutes < 60:
        return f"{minutes} min"
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


def menu_kb(ctx, user_id: int | None = None) -> InlineKeyboardMarkup:
    app_url = f"{ctx.settings.app_base_url}/app?bot_id={ctx.bot_id}"
    is_admin = user_id is not None and int(user_id) in ctx.settings.admin_ids
    rows = [
        [InlineKeyboardButton(text="🌎 Explorar salas", callback_data="rooms:list"), InlineKeyboardButton(text="🏠 Mis salas", callback_data="rooms:mine")],
        [InlineKeyboardButton(text="➕ Crear sala", callback_data="rooms:create"), InlineKeyboardButton(text="🚪 Unirme", callback_data="rooms:join")],
        [InlineKeyboardButton(text="📱 Abrir Mini App", url=app_url), InlineKeyboardButton(text="👤 Mi perfil", callback_data="profile")],
        [InlineKeyboardButton(text="📖 Manual", callback_data="help")],
    ]
    if is_admin:
        rows.insert(0, [InlineKeyboardButton(text="🛡️ Herramientas de moderación", callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _room_card(room: dict, is_member: bool = False, can_manage: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if not is_member:
        rows.append([InlineKeyboardButton(text="✅ Unirme", callback_data=f"room:join:{room['room_id']}")])
    else:
        rows.append([InlineKeyboardButton(text="📨 Seleccionar", callback_data=f"room:select:{room['room_id']}"), InlineKeyboardButton(text="🚪 Salir", callback_data=f"room:leave:{room['room_id']}")])
    if can_manage:
        rows.append([InlineKeyboardButton(text="⚙️ Gestionar", callback_data=f"room:manage:{room['room_id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _room_list_kb(rooms: list[dict], mode: str = "public") -> InlineKeyboardMarkup:
    rows = []
    for room in rooms[:15]:
        icon = "🌐" if room.get("visibility") == "PUBLIC" else "🔒"
        status_icon = {"ACTIVE": "🟢", "PAUSED": "🟠", "EXPIRED": "⏳", "CLOSED": "🔴"}.get(room.get("status"), "⚪")
        rows.append([InlineKeyboardButton(text=f"{icon} {room['name'][:26]}  ·  {status_icon} {room.get('current_members', 0)}", callback_data=f"room:view:{room['room_id']}")])
    rows.append([InlineKeyboardButton(text="➕ Crear sala", callback_data="rooms:create")])
    if mode == "public":
        rows.append([InlineKeyboardButton(text="🏠 Mis salas", callback_data="rooms:mine")])
    else:
        rows.append([InlineKeyboardButton(text="🌎 Explorar", callback_data="rooms:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _settings_kb(data: dict) -> InlineKeyboardMarkup:
    settings = data.setdefault("settings", {})
    def state(key: str) -> str:
        return "✅" if settings.get(key, True) else "☑️"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{state('allow_photo')} Fotos", callback_data="roomcreate:toggle:allow_photo"), InlineKeyboardButton(text=f"{state('allow_video')} Vídeos", callback_data="roomcreate:toggle:allow_video")],
        [InlineKeyboardButton(text=f"{state('allow_files')} Archivos", callback_data="roomcreate:toggle:allow_files"), InlineKeyboardButton(text=f"{state('allow_albums')} Álbumes", callback_data="roomcreate:toggle:allow_albums")],
        [InlineKeyboardButton(text="✅ Continuar", callback_data="roomcreate:settings_done")],
    ])


def _expiry_kb(prefix: str = "roomcreate") -> InlineKeyboardMarkup:
    values = [(0, "♾️ Sin expiración"), (10, "10 min"), (30, "30 min"), (60, "1 h"), (360, "6 h"), (720, "12 h"), (1440, "24 h"), (10080, "7 días")]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=f"{prefix}:duration:{minutes}") for minutes, text in values[:2]], [InlineKeyboardButton(text=text, callback_data=f"{prefix}:duration:{minutes}") for minutes, text in values[2:5]], [InlineKeyboardButton(text=text, callback_data=f"{prefix}:duration:{minutes}") for minutes, text in values[5:]]])


def _room_management_kb(room: dict, can_global: bool = False) -> InlineKeyboardMarkup:
    status = room.get("status")
    rows = [
        [InlineKeyboardButton(text="👥 Miembros", callback_data=f"room:members:{room['room_id']}"), InlineKeyboardButton(text="🔗 Compartir", callback_data=f"room:share:{room['room_id']}")],
        [InlineKeyboardButton(text="✏️ Editar nombre", callback_data=f"room:editname:{room['room_id']}"), InlineKeyboardButton(text="📝 Editar descripción", callback_data=f"room:editdesc:{room['room_id']}")],
        [InlineKeyboardButton(text="🎛 Permisos", callback_data=f"room:permissions:{room['room_id']}"), InlineKeyboardButton(text="⏳ Renovar", callback_data=f"room:renew:{room['room_id']}")],
    ]
    if status == "ACTIVE":
        rows.append([InlineKeyboardButton(text="⏸ Pausar", callback_data=f"room:pause:{room['room_id']}"), InlineKeyboardButton(text="🔴 Cerrar", callback_data=f"room:close:{room['room_id']}")])
    elif status == "PAUSED":
        rows.append([InlineKeyboardButton(text="▶️ Reanudar", callback_data=f"room:resume:{room['room_id']}"), InlineKeyboardButton(text="🔴 Cerrar", callback_data=f"room:close:{room['room_id']}")])
    rows.append([InlineKeyboardButton(text="↩️ Volver", callback_data=f"room:view:{room['room_id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_router(ctx) -> Router:
    router = Router(name=f"child-{ctx.bot_id}")

    async def upsert_user(message: Message) -> None:
        if message.from_user:
            await ctx.repositories.user.upsert(
                ctx.bot_id,
                int(message.from_user.id),
                first_name=message.from_user.first_name,
                username=message.from_user.username,
                language_code=message.from_user.language_code,
            )

    async def send_home(target: Message, user_id: int) -> None:
        admin = int(user_id) in ctx.settings.admin_ids
        feed_count = await ctx.repositories.admin_feed.count(ctx.bot_id) if admin else 0
        status = "🛰 Feed administrativo activo" if admin and feed_count else ""
        text = (
            f"<b>🎬 {html.escape('@' + (ctx.bot_username or 'MULTIMEDIA HUB'))}</b>\n"
            "<i>Tu espacio para compartir y descubrir contenido</i>\n\n"
            "<b>✨ Accesos rápidos</b>\n"
            "🌎 Explorar comunidades\n"
            "🏠 Gestionar tus salas\n"
            "📦 Publicar fotos, vídeos y archivos\n"
            "📱 Abrir la Mini App\n\n"
            "<b>🕶️ Privacidad</b>\n"
            "El contenido compartido puede ser revisado por la administración del servicio para moderación, seguridad y cumplimiento. "
            "En las salas, la identidad del emisor no se muestra a los demás miembros por defecto.\n"
        )
        if status:
            text += f"\n<b>🛡️ Modo administración:</b> {status}"
        await target.answer(text, reply_markup=menu_kb(ctx, user_id))

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await upsert_user(message)
        uid = int(message.from_user.id) if message.from_user else 0
        if uid in ctx.settings.admin_ids:
            await ctx.repositories.admin_feed.enable(ctx.bot_id, uid)
        # Optional deep-link: /start join_ABC123
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("join_") and message.from_user:
            code = parts[1][5:]
            room = await ctx.services.room.join_room(ctx.bot_id, uid, code)
            if room:
                await message.answer(f"✅ Te uniste a <b>{html.escape(room['name'])}</b>.\n\nAhora puedes enviar multimedia para compartirla con la sala.", reply_markup=menu_kb(ctx, uid))
                return
        await send_home(message, uid)

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            "<b>📖 MANUAL</b>\n"
            "<i>Todo lo que necesitas para empezar</i>\n\n"
            "<b>1. 🏠 Salas</b> — crea una comunidad pública o privada.\n"
            "<b>2. 🚪 Unirme</b> — usa una sala pública, código o enlace.\n"
            "<b>3. 📦 Multimedia</b> — envía fotos, vídeos, archivos o álbumes.\n"
            "<b>4. 🖼️ Álbumes</b> — se mantienen agrupados cuando Telegram lo permite.\n"
            "<b>5. 🕶️ Anonimato</b> — la identidad no se muestra a los miembros por defecto.\n"
            "<b>6. 🛡️ Moderación</b> — owners y admins pueden administrar sus salas.\n"
            "<b>7. 📱 Mini App</b> — gestiona todo desde una interfaz móvil.\n\n"
            "<b>🔐 Privacidad y seguridad</b>\n"
            "El contenido enviado puede ser revisado por la administración para seguridad y moderación."
        )

    @router.message(Command("rooms"))
    async def rooms_cmd(message: Message) -> None:
        rooms = await ctx.repositories.room.list_public(ctx.bot_id, 15)
        if not rooms:
            await message.answer("🌎 <b>Explorar salas</b>\n\nNo hay salas públicas activas todavía.", reply_markup=menu_kb(ctx, int(message.from_user.id)))
            return
        await message.answer("<b>🌎 Comunidades públicas</b>\n\nElige una sala para ver sus detalles:", reply_markup=_room_list_kb(rooms, "public"))

    @router.message(Command("my_rooms"))
    async def my_rooms(message: Message) -> None:
        rooms = await ctx.repositories.room.list_for_user(ctx.bot_id, int(message.from_user.id), 20)
        if not rooms:
            await message.answer("🏠 <b>Mis salas</b>\n\nTodavía no perteneces a ninguna sala.", reply_markup=menu_kb(ctx, int(message.from_user.id)))
            return
        await message.answer("<b>🏠 Mis salas</b>\n\nSelecciona una para entrar o gestionarla:", reply_markup=_room_list_kb(rooms, "mine"))

    @router.message(Command("join"))
    async def join_cmd(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await ctx.repositories.session.set(ctx.bot_id, int(message.from_user.id), "join_ref", {})
            await message.answer("🚪 Envíame el <b>ID de la sala</b> o el <b>código de invitación</b>.")
            return
        room = await ctx.services.room.join_room(ctx.bot_id, int(message.from_user.id), parts[1].strip())
        if not room:
            await message.answer("❌ No se encontró la sala o no está disponible.")
            return
        await message.answer(f"✅ Te uniste a <b>{html.escape(room['name'])}</b>.\n\nEsta sala quedó seleccionada como destino de publicación.", reply_markup=_room_card(room, True, await ctx.services.room.can_manage(ctx.bot_id, room['room_id'], int(message.from_user.id), int(message.from_user.id) in ctx.settings.admin_ids)))

    @router.message(Command("create_room"))
    async def create_room_cmd(message: Message) -> None:
        await ctx.repositories.session.set(ctx.bot_id, int(message.from_user.id), "room_create_name", {"settings": {"allow_photo": True, "allow_video": True, "allow_files": True, "allow_albums": True}})
        await message.answer("<b>➕ Crear sala</b>\n\nEmpecemos.\n\nEscribe el <b>nombre</b> de la sala (máx. 80 caracteres).")

    @router.callback_query(F.data == "rooms:list")
    async def list_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        rooms = await ctx.repositories.room.list_public(ctx.bot_id, 15)
        if not rooms:
            await callback.message.edit_text("🌎 <b>Comunidades públicas</b>\n\nTodavía no hay salas activas.", reply_markup=menu_kb(ctx, int(callback.from_user.id)))
            return
        await callback.message.edit_text("<b>🌎 Comunidades públicas</b>\n\nElige una:", reply_markup=_room_list_kb(rooms, "public"))

    @router.callback_query(F.data == "rooms:mine")
    async def mine_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        rooms = await ctx.repositories.room.list_for_user(ctx.bot_id, int(callback.from_user.id), 20)
        if not rooms:
            await callback.message.edit_text("🏠 <b>Mis salas</b>\n\nAún no perteneces a ninguna.", reply_markup=menu_kb(ctx, int(callback.from_user.id)))
            return
        await callback.message.edit_text("<b>🏠 Mis salas</b>\n\nElige una:", reply_markup=_room_list_kb(rooms, "mine"))

    @router.callback_query(F.data == "rooms:join")
    async def join_callback_menu(callback: CallbackQuery) -> None:
        await callback.answer()
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "join_ref", {})
        await callback.message.answer("🚪 Envíame el <b>ID</b> o <b>código</b> de la sala que quieres usar.")

    @router.callback_query(F.data == "rooms:create")
    async def create_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "room_create_name", {"settings": {"allow_photo": True, "allow_video": True, "allow_files": True, "allow_albums": True}})
        await callback.message.answer("<b>➕ Nueva sala</b>\n\nEscribe el nombre de la sala.")

    @router.callback_query(F.data == "profile")
    async def profile(callback: CallbackQuery) -> None:
        await callback.answer()
        user = await ctx.repositories.user.get(ctx.bot_id, int(callback.from_user.id)) or {}
        rooms = await ctx.repositories.room.list_for_user(ctx.bot_id, int(callback.from_user.id), 100)
        created = sum(1 for room in rooms if int(room.get("owner_id", 0)) == int(callback.from_user.id))
        active = sum(1 for room in rooms if room.get("status") == "ACTIVE")
        await callback.message.edit_text(
            "<b>👤 Mi perfil</b>\n\n"
            f"🆔 <code>{callback.from_user.id}</code>\n"
            f"🌐 Idioma: {html.escape(str(user.get('language_code') or 'es'))}\n"
            f"🏠 Salas: <b>{len(rooms)}</b>\n"
            f"✨ Creadas por mí: <b>{created}</b>\n"
            f"🟢 Activas: <b>{active}</b>\n\n"
            "Tu identidad nunca se expone a los demás miembros por defecto.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Inicio", callback_data="home")]]),
        )

    @router.callback_query(F.data == "home")
    async def home_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"<b>🎬 @{html.escape(ctx.bot_username or 'MULTIMEDIA HUB')}</b>\n\nSelecciona una opción:",
                reply_markup=menu_kb(ctx, int(callback.from_user.id)),
            )
        except Exception:
            await callback.message.answer("Selecciona una opción:", reply_markup=menu_kb(ctx, int(callback.from_user.id)))

    @router.callback_query(F.data == "help")
    async def help_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await help_cmd(callback.message)

    @router.callback_query(F.data == "admin:panel")
    async def admin_panel(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid not in ctx.settings.admin_ids:
            await callback.answer("No autorizado", show_alert=True)
            return
        enabled = await ctx.repositories.admin_feed.is_enabled(ctx.bot_id, uid)
        status = "🟢 ACTIVO" if enabled else "⚪ INACTIVO"
        text = (
            "<b>🛡️ HERRAMIENTAS DE MODERACIÓN</b>\n"
            "<i>Acceso exclusivo para administradores</i>\n\n"
            f"📥 Recepción directa: <b>{status}</b>\n"
            "📊 Puedes consultar la actividad y la salud del bot desde el panel.\n\n"
            "El contenido se recibe para tareas de moderación y seguridad del servicio."
        )
        rows = [
            [InlineKeyboardButton(text=("🔕 Desactivar recepción" if enabled else "🔔 Activar recepción"), callback_data="adminfeed:toggle")],
            [InlineKeyboardButton(text="↩️ Volver al inicio", callback_data="home")],
        ]
        await callback.answer()
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data == "adminfeed:toggle")
    async def adminfeed_toggle(callback: CallbackQuery) -> None:
        uid = int(callback.from_user.id)
        if uid not in ctx.settings.admin_ids:
            await callback.answer("No autorizado", show_alert=True)
            return
        enabled = await ctx.repositories.admin_feed.is_enabled(ctx.bot_id, uid)
        if enabled:
            await ctx.repositories.admin_feed.disable(ctx.bot_id, uid)
            notice = "Recepción desactivada"
        else:
            await ctx.repositories.admin_feed.enable(ctx.bot_id, uid)
            notice = "Recepción activada"
        enabled = not enabled
        status = "🟢 ACTIVO" if enabled else "⚪ INACTIVO"
        text = (
            "<b>🛡️ HERRAMIENTAS DE MODERACIÓN</b>\n"
            "<i>Acceso exclusivo para administradores</i>\n\n"
            f"📥 Recepción directa: <b>{status}</b>\n"
            "📊 Puedes consultar la actividad y la salud del bot desde el panel.\n\n"
            "El contenido se recibe para tareas de moderación y seguridad del servicio."
        )
        rows = [
            [InlineKeyboardButton(text=("🔕 Desactivar recepción" if enabled else "🔔 Activar recepción"), callback_data="adminfeed:toggle")],
            [InlineKeyboardButton(text="↩️ Volver al inicio", callback_data="home")],
        ]
        await callback.answer(notice)
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("room:view:"))
    async def room_view(callback: CallbackQuery, do_answer: bool = True) -> None:
        if do_answer:
            await callback.answer()
        room_id = callback.data.split(":", 2)[2]
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        if not room:
            await callback.message.edit_text("❌ Esta sala ya no existe.", reply_markup=menu_kb(ctx, int(callback.from_user.id)))
            return
        uid = int(callback.from_user.id)
        member = await ctx.repositories.room.get_membership(ctx.bot_id, room_id, uid)
        is_member = bool(member and not member.get("banned"))
        can_manage = await ctx.services.room.can_manage(ctx.bot_id, room_id, uid, uid in ctx.settings.admin_ids)
        expires = room.get("expires_at")
        expires_text = "sin expiración"
        if expires:
            expires_text = expires.strftime("%d/%m %H:%M UTC")
        text = (
            f"<b>{'🌐' if room.get('visibility') == 'PUBLIC' else '🔒'} {html.escape(room['name'])}</b>\n\n"
            f"{html.escape(room.get('description') or 'Sin descripción')}\n\n"
            f"👥 <b>{room.get('current_members', 0)}</b>/{room.get('max_members', '∞')} miembros\n"
            f"🟢 Estado: <b>{room.get('status')}</b>\n"
            f"⏳ Expira: <b>{expires_text}</b>\n\n"
            "📦 Multimedia anónima: <b>activa</b>"
        )
        await callback.message.edit_text(text, reply_markup=_room_card(room, is_member=is_member, can_manage=can_manage))

    @router.callback_query(F.data.startswith("room:join:"))
    async def room_join(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        room = await ctx.services.room.join_room(ctx.bot_id, int(callback.from_user.id), room_id)
        if not room:
            await callback.answer("❌ No disponible", show_alert=True)
            return
        await callback.answer("✅ Unido")
        await room_view(callback, do_answer=False)

    @router.callback_query(F.data.startswith("room:select:"))
    async def room_select(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        ok = await ctx.services.room.set_active_room(ctx.bot_id, int(callback.from_user.id), room_id)
        await callback.answer("✅ Sala seleccionada" if ok else "❌ No disponible", show_alert=not ok)

    @router.callback_query(F.data.startswith("room:leave:"))
    async def room_leave(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        ok = await ctx.services.room.leave_room(ctx.bot_id, int(callback.from_user.id), room_id)
        await callback.answer("✅ Has salido" if ok else "❌ No se puede salir", show_alert=not ok)
        if ok:
            await callback.message.edit_text("🏠 Has salido de la sala.", reply_markup=menu_kb(ctx, int(callback.from_user.id)))

    @router.callback_query(F.data.startswith("room:manage:"))
    async def room_manage(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        if not room or not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True)
            return
        await callback.answer()
        await callback.message.edit_text(f"<b>⚙️ Gestionar {html.escape(room['name'])}</b>\n\nEstado: {room['status']}\nMiembros: {room['current_members']}", reply_markup=_room_management_kb(room))

    @router.callback_query(F.data.startswith("room:share:"))
    async def room_share(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        if not room:
            await callback.answer("Sala no disponible", show_alert=True); return
        url = await ctx.services.room.build_share_url(ctx.bot_username, room["invite_code"])
        await callback.answer("Enlace listo")
        await callback.message.answer(f"🔗 <b>Compartir sala</b>\n\n<code>{html.escape(url)}</code>")

    @router.callback_query(F.data.startswith("room:pause:"))
    async def room_pause(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        await ctx.repositories.room.pause(ctx.bot_id, room_id)
        await callback.answer("⏸ Sala pausada")
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        await callback.message.edit_text(f"<b>⚙️ {html.escape(room['name'])}</b>\n\nEstado: PAUSED", reply_markup=_room_management_kb(room))

    @router.callback_query(F.data.startswith("room:resume:"))
    async def room_resume(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        await ctx.repositories.room.resume(ctx.bot_id, room_id)
        await callback.answer("▶️ Sala activa")
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        await callback.message.edit_text(f"<b>⚙️ {html.escape(room['name'])}</b>\n\nEstado: ACTIVE", reply_markup=_room_management_kb(room))

    @router.callback_query(F.data.startswith("room:close:"))
    async def room_close(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        await ctx.repositories.room.close(ctx.bot_id, room_id)
        await callback.answer("Sala cerrada", show_alert=True)
        await callback.message.edit_text("🔴 <b>Sala cerrada</b>\n\nLa sala ya no admite nuevas publicaciones.", reply_markup=menu_kb(ctx, int(callback.from_user.id)))

    @router.callback_query(F.data.startswith("room:members:"))
    async def room_members(callback: CallbackQuery, do_answer: bool = True) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        if do_answer:
            await callback.answer()
        members = await ctx.repositories.room.list_members(ctx.bot_id, room_id, 20)
        lines = ["<b>👥 Miembros</b>", ""]
        rows = []
        for member in members:
            uid = int(member["user_id"])
            user_doc = await ctx.repositories.user.get(ctx.bot_id, uid) or {}
            label = html.escape(user_doc.get("username") or user_doc.get("first_name") or str(uid))
            role = member.get("role", "MEMBER")
            flags = " 🔇" if member.get("muted") else ""
            if member.get("banned"):
                flags += " 🚫"
            lines.append(f"• <code>{uid}</code> · {label} · {role}{flags}")
            if uid != int(room["owner_id"]):
                rows.append([InlineKeyboardButton(text=f"🔇/🔊 {uid}", callback_data=f"member:mute:{room_id}:{uid}"), InlineKeyboardButton(text=f"🚫/✅ {uid}", callback_data=f"member:ban:{room_id}:{uid}")])
                rows.append([InlineKeyboardButton(text=f"👢 Expulsar {uid}", callback_data=f"member:kick:{room_id}:{uid}")])
        rows.append([InlineKeyboardButton(text="↩️ Volver", callback_data=f"room:manage:{room_id}")])
        await callback.message.edit_text("\n".join(lines[:22]), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows[:40]))

    @router.callback_query(F.data.startswith("member:mute:"))
    async def member_mute(callback: CallbackQuery) -> None:
        _, _, room_id, user_id = callback.data.split(":", 3)
        target = int(user_id)
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        member = await ctx.repositories.room.get_membership(ctx.bot_id, room_id, target)
        if not member:
            await callback.answer("Miembro inexistente", show_alert=True); return
        await ctx.repositories.room.update_member(ctx.bot_id, room_id, target, muted=not bool(member.get("muted")))
        await callback.answer("🔇 Actualizado")
        await room_members(callback, do_answer=False)

    @router.callback_query(F.data.startswith("member:ban:"))
    async def member_ban(callback: CallbackQuery) -> None:
        _, _, room_id, user_id = callback.data.split(":", 3)
        target = int(user_id)
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        member = await ctx.repositories.room.get_membership(ctx.bot_id, room_id, target)
        if not member:
            await callback.answer("Miembro inexistente", show_alert=True); return
        await ctx.repositories.room.update_member(ctx.bot_id, room_id, target, banned=not bool(member.get("banned")))
        await callback.answer("🚫 Estado actualizado")
        await room_members(callback, do_answer=False)

    @router.callback_query(F.data.startswith("member:kick:"))
    async def member_kick(callback: CallbackQuery) -> None:
        _, _, room_id, user_id = callback.data.split(":", 3)
        target = int(user_id)
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        await ctx.services.room.leave_room(ctx.bot_id, target, room_id)
        await callback.answer("👢 Expulsado")
        await room_members(callback, do_answer=False)

    @router.callback_query(F.data.startswith("room:permissions:"))
    async def room_permissions(callback: CallbackQuery, do_answer: bool = True) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        if do_answer:
            await callback.answer()
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        settings = room.get("settings", {})
        rows = []
        for key, label in [("allow_photo", "Fotos"), ("allow_video", "Vídeos"), ("allow_files", "Archivos"), ("allow_albums", "Álbumes"), ("anonymous_media", "Anonimato")]:
            rows.append([InlineKeyboardButton(text=("✅ " if settings.get(key, True) else "❌ ") + label, callback_data=f"roomperm:{room_id}:{key}")])
        rows.append([InlineKeyboardButton(text="↩️ Volver", callback_data=f"room:manage:{room_id}")])
        await callback.message.edit_text("<b>🎛 Permisos de la sala</b>\n\nActiva o desactiva características:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("roomperm:"))
    async def roomperm_toggle(callback: CallbackQuery) -> None:
        _, room_id, key = callback.data.split(":", 2)
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        room = await ctx.repositories.room.get(ctx.bot_id, room_id)
        current = bool(room.get("settings", {}).get(key, True))
        new_settings = dict(room.get("settings", {}))
        new_settings[key] = not current
        await ctx.repositories.room.update_room(ctx.bot_id, room_id, settings=new_settings)
        await callback.answer("✅ Actualizado")
        await room_permissions(callback, do_answer=False)

    @router.callback_query(F.data.startswith("room:editname:"))
    async def edit_name(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "room_edit_name", {"room_id": room_id})
        await callback.answer()
        await callback.message.answer("✏️ Escribe el nuevo nombre de la sala.")

    @router.callback_query(F.data.startswith("room:editdesc:"))
    async def edit_desc(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "room_edit_desc", {"room_id": room_id})
        await callback.answer()
        await callback.message.answer("📝 Escribe la nueva descripción.")

    @router.callback_query(F.data.startswith("room:renew:"))
    async def renew_room(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        if not await ctx.services.room.can_manage(ctx.bot_id, room_id, int(callback.from_user.id), int(callback.from_user.id) in ctx.settings.admin_ids):
            await callback.answer("No autorizado", show_alert=True); return
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "room_renew", {"room_id": room_id})
        await callback.answer()
        await callback.message.answer("⏳ Selecciona la nueva duración:", reply_markup=_expiry_kb("roomrenew"))

    @router.callback_query(F.data.startswith("roomrenew:duration:"))
    async def roomrenew_duration(callback: CallbackQuery) -> None:
        _, _, minutes = callback.data.split(":")
        session = await ctx.repositories.session.get(ctx.bot_id, int(callback.from_user.id))
        room_id = (session or {}).get("data", {}).get("room_id")
        if room_id:
            expiry = None if int(minutes) == 0 else datetime_utc_plus(int(minutes))
            await ctx.repositories.room.update_room(ctx.bot_id, room_id, expires_at=expiry)
            await ctx.repositories.session.clear(ctx.bot_id, int(callback.from_user.id))
            await callback.answer("✅ Expiración actualizada")
            await callback.message.answer(f"⏳ Nueva duración: {_duration_text(int(minutes))}")
        else:
            await callback.answer("Sesión expirada", show_alert=True)

    @router.callback_query(F.data.startswith("roomcreate:visibility:"))
    async def roomcreate_visibility(callback: CallbackQuery) -> None:
        _, _, value = callback.data.split(":")
        session = await ctx.repositories.session.get(ctx.bot_id, int(callback.from_user.id))
        data = (session or {}).get("data", {})
        data["visibility"] = value
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "room_create_max", data)
        await callback.answer("✅ Configurado")
        await callback.message.answer("👥 ¿Cuántos miembros puede tener? Responde con un número (0 = sin límite práctico).")

    @router.callback_query(F.data.startswith("roomcreate:toggle:"))
    async def roomcreate_toggle(callback: CallbackQuery) -> None:
        key = callback.data.split(":", 2)[2]
        session = await ctx.repositories.session.get(ctx.bot_id, int(callback.from_user.id))
        data = (session or {}).get("data", {})
        settings = data.setdefault("settings", {})
        settings[key] = not bool(settings.get(key, True))
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "room_create_settings", data)
        await callback.message.edit_reply_markup(reply_markup=_settings_kb(data))
        await callback.answer("Actualizado")

    @router.callback_query(F.data == "roomcreate:settings_done")
    async def roomcreate_settings_done(callback: CallbackQuery) -> None:
        session = await ctx.repositories.session.get(ctx.bot_id, int(callback.from_user.id))
        data = (session or {}).get("data", {})
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "room_create_duration", data)
        await callback.answer()
        await callback.message.edit_text("⏳ ¿Cuánto tiempo debe permanecer activa la sala?", reply_markup=_expiry_kb())

    @router.callback_query(F.data.startswith("roomcreate:duration:"))
    async def roomcreate_duration(callback: CallbackQuery) -> None:
        minutes = int(callback.data.split(":")[-1])
        session = await ctx.repositories.session.get(ctx.bot_id, int(callback.from_user.id))
        data = (session or {}).get("data", {})
        data["duration_minutes"] = minutes
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "room_create_confirm", data)
        text = (
            "<b>✅ Revisa tu sala</b>\n\n"
            f"🏷 Nombre: <b>{html.escape(data.get('name', ''))}</b>\n"
            f"📝 Descripción: {html.escape(data.get('description', '') or 'Sin descripción')}\n"
            f"🌐 Tipo: <b>{'Pública' if data.get('visibility') == 'PUBLIC' else 'Privada'}</b>\n"
            f"👥 Capacidad: <b>{data.get('max_members')}</b>\n"
            f"⏳ Duración: <b>{_duration_text(minutes)}</b>\n\n"
            "¿Crear sala?"
        )
        await callback.answer()
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Crear ahora", callback_data="roomcreate:confirm")], [InlineKeyboardButton(text="✖️ Cancelar", callback_data="roomcreate:cancel")]]))

    @router.callback_query(F.data == "roomcreate:confirm")
    async def roomcreate_confirm(callback: CallbackQuery) -> None:
        session = await ctx.repositories.session.get(ctx.bot_id, int(callback.from_user.id))
        data = (session or {}).get("data", {})
        if not data.get("name"):
            await callback.answer("La sesión expiró", show_alert=True); return
        room = await ctx.services.room.create_room(
            ctx.bot_id,
            int(callback.from_user.id),
            data["name"],
            data.get("description", ""),
            data.get("visibility", "PUBLIC"),
            int(data.get("max_members") or 1),
            settings=data.get("settings", {}),
            duration_minutes=int(data.get("duration_minutes") or 0),
        )
        await ctx.repositories.session.clear(ctx.bot_id, int(callback.from_user.id))
        await callback.answer("🎉 Sala creada")
        await callback.message.edit_text(
            f"<b>🎉 Sala creada</b>\n\n"
            f"🏷 <b>{html.escape(room['name'])}</b>\n"
            f"🆔 <code>{room['room_id']}</code>\n"
            f"🔐 Código: <code>{room['invite_code']}</code>\n\n"
            "La sala quedó seleccionada para tus próximas publicaciones.",
            reply_markup=_room_card(room, True, True),
        )

    @router.callback_query(F.data == "roomcreate:cancel")
    async def roomcreate_cancel(callback: CallbackQuery) -> None:
        await ctx.repositories.session.clear(ctx.bot_id, int(callback.from_user.id))
        await callback.answer("Cancelado")
        await callback.message.edit_text("❎ Creación cancelada.", reply_markup=menu_kb(ctx, int(callback.from_user.id)))

    @router.message()
    async def generic(message: Message) -> None:
        if not message.from_user:
            return
        uid = int(message.from_user.id)
        if message.text and not message.text.startswith("/"):
            session = await ctx.repositories.session.get(ctx.bot_id, uid)
            if session:
                step = session.get("step")
                data = session.get("data", {})
                if step == "join_ref":
                    await ctx.repositories.session.clear(ctx.bot_id, uid)
                    room = await ctx.services.room.join_room(ctx.bot_id, uid, message.text.strip())
                    await message.answer("✅ Unido a la sala." if room else "❌ No se encontró la sala.", reply_markup=menu_kb(ctx, uid))
                    return
                if step == "room_create_name":
                    data["name"] = message.text[:80].strip()
                    await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_description", data)
                    await message.answer("📝 Escribe una descripción (o escribe <code>-</code> para omitir).")
                    return
                if step == "room_create_description":
                    data["description"] = "" if message.text.strip() == "-" else message.text[:500].strip()
                    await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_visibility", data)
                    await message.answer("🌐 Elige la visibilidad:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🌎 Pública", callback_data="roomcreate:visibility:PUBLIC"), InlineKeyboardButton(text="🔒 Privada", callback_data="roomcreate:visibility:PRIVATE")]]))
                    return
                if step == "room_create_max":
                    if not message.text.strip().isdigit():
                        await message.answer("Escribe un número válido.")
                        return
                    data["max_members"] = int(message.text.strip()) or 999999
                    await ctx.repositories.session.set(ctx.bot_id, uid, "room_create_settings", data)
                    await message.answer("🎛 Elige los permisos de multimedia:", reply_markup=_settings_kb(data))
                    return
                if step == "room_edit_name":
                    room_id = data.get("room_id")
                    await ctx.repositories.room.update_room(ctx.bot_id, room_id, name=message.text)
                    await ctx.repositories.session.clear(ctx.bot_id, uid)
                    await message.answer("✅ Nombre actualizado.")
                    return
                if step == "room_edit_desc":
                    room_id = data.get("room_id")
                    await ctx.repositories.room.update_room(ctx.bot_id, room_id, description=message.text)
                    await ctx.repositories.session.clear(ctx.bot_id, uid)
                    await message.answer("✅ Descripción actualizada.")
                    return
        if message.photo or message.video or message.document or message.animation or message.text:
            if message.text and message.text.startswith("/"):
                return
            await ctx.services.media.handle_message(message)

    @router.errors()
    async def errors(event) -> None:
        ctx.logger.exception("child_handler_error", exc_info=event.exception)

    return router


def datetime_utc_plus(minutes: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
