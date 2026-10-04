from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message


def build_master_router(manager) -> Router:
    router = Router(name="master")

    def is_admin(message: Message) -> bool:
        return bool(message.from_user and int(message.from_user.id) in manager.settings.admin_ids)

    async def denied(message: Message) -> None:
        await message.answer("⛔ No tienes permisos de administrador.")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if not is_admin(message):
            await denied(message)
            return
        await message.answer("<b>Master Control Plane</b>\n\n/bots\n/add_bot\n/bot_info ID\n/bot_start ID\n/bot_stop ID\n/bot_restart ID\n/bot_enable ID\n/bot_disable ID\n/bot_delete ID\n/bot_health\n/stats")

    @router.message(Command("bots"))
    async def bots(message: Message) -> None:
        if not is_admin(message):
            await denied(message); return
        docs = await manager.repositories.bots.list_all()
        if not docs:
            await message.answer("No hay bots hijos registrados."); return
        lines = [f"<code>{d['bot_id']}</code> @{d.get('username','-')} — {d.get('status')} — {'ON' if d.get('enabled') else 'OFF'}" for d in docs]
        await message.answer("<b>Bots</b>\n" + "\n".join(lines))

    @router.message(Command("add_bot"))
    async def add_bot(message: Message) -> None:
        if not is_admin(message):
            await denied(message); return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2:
            token = parts[1].strip()
            try:
                await manager.register_bot(token, int(message.from_user.id))
                await message.answer("✅ Bot hijo registrado y arrancando.")
            finally:
                try:
                    await message.delete()
                except Exception:
                    pass
            return
        await manager.repositories.session.set(0, int(message.from_user.id), "master_token", {})
        await message.answer("Envía ahora el token del bot hijo. Se validará y luego se eliminará este mensaje.")

    @router.message(Command("bot_info"))
    async def bot_info(message: Message) -> None:
        if not is_admin(message):
            await denied(message); return
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Uso: /bot_info ID"); return
        info = await manager.get_info(int(parts[1]))
        await message.answer(manager.format_bot_info(info))

    async def action(message: Message, name: str) -> None:
        if not is_admin(message):
            await denied(message); return
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer(f"Uso: /{name} ID"); return
        bot_id = int(parts[1])
        try:
            result = await getattr(manager, name)(bot_id)
            await message.answer(f"✅ {result}")
        except Exception as exc:
            await message.answer(f"❌ {type(exc).__name__}: {str(exc)[:300]}")

    for command_name in ("bot_start", "bot_stop", "bot_restart", "bot_enable", "bot_disable", "bot_delete"):
        router.message.register(lambda message, n=command_name: action(message, n), Command(command_name))

    @router.message(Command("bot_health"))
    async def bot_health(message: Message) -> None:
        if not is_admin(message):
            await denied(message); return
        lines = []
        for bot_id, runtime in manager.registry.items():
            lines.append(f"{bot_id}: {runtime.status} tasks={runtime.task_registry.count if runtime.task_registry else 0} queue={runtime.broadcast_queue.qsize() if runtime.broadcast_queue else 0}")
        await message.answer("<b>Health</b>\n" + ("\n".join(lines) or "sin bots"))

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        if not is_admin(message):
            await denied(message); return
        running = sum(1 for r in manager.registry.values() if str(r.status) == "RUNNING")
        await message.answer(f"Bots en registry: {len(manager.registry)}\nRunning: {running}")

    @router.message()
    async def master_session(message: Message) -> None:
        if not is_admin(message) or not message.text:
            return
        session = await manager.repositories.session.get(0, int(message.from_user.id))
        if session and session.get("step") == "master_token":
            token = message.text.strip()
            await manager.repositories.session.clear(0, int(message.from_user.id))
            try:
                await manager.register_bot(token, int(message.from_user.id))
                await message.answer("✅ Bot registrado correctamente.")
            except Exception as exc:
                await message.answer(f"❌ No se pudo registrar: {type(exc).__name__}")
            finally:
                try:
                    await message.delete()
                except Exception:
                    pass

    return router
