from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


def menu_kb(ctx) -> InlineKeyboardMarkup:
    app_url = f"{ctx.settings.app_base_url}/app?bot_id={ctx.bot_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Salas", callback_data="rooms:list"), InlineKeyboardButton(text="➕ Crear sala", callback_data="rooms:create")],
        [InlineKeyboardButton(text="🚪 Unirme", callback_data="rooms:join"), InlineKeyboardButton(text="📱 Mini App", url=app_url)],
        [InlineKeyboardButton(text="ℹ️ Ayuda", callback_data="help")],
    ])


def build_router(ctx) -> Router:
    router = Router(name=f"child-{ctx.bot_id}")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if message.from_user:
            await ctx.repositories.user.upsert(ctx.bot_id, int(message.from_user.id), first_name=message.from_user.first_name, username=message.from_user.username)
        await message.answer(
            f"<b>🤖 @{ctx.bot.username or 'bot'}</b>\n\nIntercambia fotos, vídeos y archivos de forma anónima dentro de salas.\n\nSelecciona una opción:",
            reply_markup=menu_kb(ctx),
        )

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer("<b>Ayuda</b>\n\n/rooms — salas públicas\n/create_room — crear sala\n/join ROOM-ID-O-CODIGO — unirse\n/my_rooms — mis salas\n\nDespués de seleccionar una sala, envía fotos, vídeos o archivos.")

    @router.message(Command("rooms"))
    async def rooms_cmd(message: Message) -> None:
        rooms = await ctx.services.room.room_repo.list_public(ctx.bot_id, 15)
        if not rooms:
            await message.answer("No hay salas públicas activas.", reply_markup=menu_kb(ctx))
            return
        rows = []
        for room in rooms:
            rows.append([InlineKeyboardButton(text=f"🎬 {room['name']} · 👥 {room.get('current_members',0)}", callback_data=f"room:join:{room['room_id']}")])
        await message.answer("<b>Salas públicas</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.message(Command("my_rooms"))
    async def my_rooms(message: Message) -> None:
        rooms = await ctx.services.room.room_repo.list_for_user(ctx.bot_id, int(message.from_user.id), 20)
        if not rooms:
            await message.answer("Todavía no estás en ninguna sala.")
            return
        lines = [f"<b>{r['name']}</b> — {r['room_id']} — {r['current_members']} miembros" for r in rooms]
        await message.answer("\n".join(lines))

    @router.message(Command("join"))
    async def join_cmd(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Uso: <code>/join ROOM-XXXX</code> o <code>/join CODIGO</code>")
            return
        room = await ctx.services.room.join_room(ctx.bot_id, int(message.from_user.id), parts[1].strip())
        if not room:
            await message.answer("No se encontró la sala o está llena/cerrada.")
            return
        await message.answer(f"✅ Unido a <b>{room['name']}</b>. Ahora envía multimedia para compartirla de forma anónima.")

    @router.message(Command("create_room"))
    async def create_room_cmd(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2:
            bits = [x.strip() for x in parts[1].split("|")]
            name = bits[0]
            description = bits[1] if len(bits) > 1 else ""
            visibility = bits[2].upper() if len(bits) > 2 else "PUBLIC"
            max_members = int(bits[3]) if len(bits) > 3 and bits[3].isdigit() else 100
            room = await ctx.services.room.create_room(ctx.bot_id, int(message.from_user.id), name, description, visibility, max_members)
            await message.answer(f"✅ Sala creada\n\nNombre: <b>{room['name']}</b>\nID: <code>{room['room_id']}</code>\nCódigo: <code>{room['invite_code']}</code>\nTipo: {room['visibility']}")
            return
        await ctx.repositories.session.set(ctx.bot_id, int(message.from_user.id), "name", {})
        await message.answer("Escribe el <b>nombre</b> de la sala.")

    @router.callback_query(F.data.startswith("room:join:"))
    async def join_callback(callback: CallbackQuery) -> None:
        room_id = callback.data.split(":", 2)[2]
        room = await ctx.services.room.join_room(ctx.bot_id, int(callback.from_user.id), room_id)
        await callback.answer("✅ Unido" if room else "No disponible")
        if room:
            await callback.message.answer(f"Entraste a <b>{room['name']}</b>.")

    @router.callback_query(F.data == "rooms:list")
    async def list_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await rooms_cmd(callback.message)

    @router.callback_query(F.data == "rooms:join")
    async def join_callback_menu(callback: CallbackQuery) -> None:
        await callback.answer()
        await callback.message.answer("Usa <code>/join ROOM-ID</code> o <code>/join CODIGO</code>.")

    @router.callback_query(F.data == "rooms:create")
    async def create_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await ctx.repositories.session.set(ctx.bot_id, int(callback.from_user.id), "name", {})
        await callback.message.answer("Escribe el <b>nombre</b> de la sala.")

    @router.callback_query(F.data == "help")
    async def help_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        await help_cmd(callback.message)

    @router.message()
    async def generic(message: Message) -> None:
        if not message.from_user:
            return
        uid = int(message.from_user.id)
        session = await ctx.repositories.session.get(ctx.bot_id, uid)
        if session and message.text and not message.text.startswith("/"):
            step = session.get("step")
            data = session.get("data", {})
            if step == "name":
                data["name"] = message.text[:80]
                await ctx.repositories.session.set(ctx.bot_id, uid, "description", data)
                await message.answer("Ahora escribe la <b>descripción</b>.")
                return
            if step == "description":
                data["description"] = message.text[:500]
                await ctx.repositories.session.set(ctx.bot_id, uid, "visibility", data)
                await message.answer("Escribe <code>PUBLIC</code> o <code>PRIVATE</code>.")
                return
            if step == "visibility":
                vis = message.text.strip().upper()
                if vis not in {"PUBLIC", "PRIVATE"}:
                    await message.answer("Responde PUBLIC o PRIVATE.")
                    return
                data["visibility"] = vis
                await ctx.repositories.session.set(ctx.bot_id, uid, "max_members", data)
                await message.answer("Límite de miembros (número, 0 = ilimitado):")
                return
            if step == "max_members":
                if not message.text.strip().isdigit():
                    await message.answer("Escribe un número válido.")
                    return
                data["max_members"] = int(message.text.strip()) or 999999
                room = await ctx.services.room.create_room(ctx.bot_id, uid, data["name"], data["description"], data["visibility"], data["max_members"])
                await ctx.repositories.session.clear(ctx.bot_id, uid)
                await message.answer(f"✅ Sala creada\n\n<b>{room['name']}</b>\nID: <code>{room['room_id']}</code>\nCódigo: <code>{room['invite_code']}</code>")
                return
        if message.photo or message.video or message.document or message.animation:
            await ctx.services.media.handle_message(message)
            return
        if message.text and not message.text.startswith("/"):
            await message.answer("Usa el menú o /help.")

    @router.errors()
    async def errors(event) -> None:
        ctx.logger.exception("child_handler_error", exc_info=event.exception)

    return router
