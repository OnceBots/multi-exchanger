from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from app.bot.child_handlers import build_router
from app.bot.runtime import BotRuntime
from app.core.context import BotContext
from app.core.enums import BotStatus
from app.core.exceptions import BotAlreadyRunningError, BotNotFoundError
from app.core.models import BotConfig, BotInfo
from app.core.task_registry import TaskRegistry
from app.services.token_service import TokenService
from app.services.webapp_auth import validate_init_data
from app.utils.backoff import backoff_delay


class BotRegistry(dict[int, BotRuntime]):
    pass


class RepositoryBundle:
    def __init__(self, mongo) -> None:
        from app.db.repositories.admin_feed import AdminFeedRepository
        from app.db.repositories.audit import AuditRepository
        from app.db.repositories.bots import BotRepository
        from app.db.repositories.media import MediaRepository
        from app.db.repositories.rooms import RoomRepository
        from app.db.repositories.sessions import SessionRepository
        from app.db.repositories.users import UserRepository
        self.bots = BotRepository(mongo)
        self.user = UserRepository(mongo)
        self.room = RoomRepository(mongo)
        self.media = MediaRepository(mongo)
        self.session = SessionRepository(mongo)
        self.audit = AuditRepository(mongo)
        self.admin_feed = AdminFeedRepository(mongo)


class BotManager:
    def __init__(self, settings, mongo, secret_box) -> None:
        self.settings = settings
        self.mongo = mongo
        self.repositories = RepositoryBundle(mongo)
        self.token_service = TokenService(secret_box, settings.child_webhook_secret_length)
        self.registry: BotRegistry = BotRegistry()
        self.logger = logging.getLogger("bot.manager")
        self.lock = asyncio.Lock()
        self.supervisor_task: asyncio.Task[object] | None = None
        self.room_cleanup_task: asyncio.Task[object] | None = None
        self.master_bot = Bot(settings.master_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.master_dp = Dispatcher()
        self._failure_times: dict[int, deque[float]] = defaultdict(deque)
        self.shutting_down = False

    async def start_master(self) -> None:
        me = await self.master_bot.get_me()
        self.logger.info("master_ready username=@%s id=%s", me.username, me.id)
        from app.bot.master_handlers import build_master_router
        self.master_dp.include_router(build_master_router(self))
        if self.settings.mode == "webhook":
            url = self.settings.webhook_base_url + self.settings.master_webhook_path
            await self.master_bot.set_webhook(url=url, secret_token=self.settings.webhook_secret, allowed_updates=self.master_dp.resolve_used_update_types(), max_connections=self.settings.telegram_max_connections, drop_pending_updates=self.settings.drop_pending_updates)
            info = await self.master_bot.get_webhook_info()
            if info.url != url:
                raise RuntimeError("El webhook del Master no coincide con la configuración")
            self.logger.info("master_webhook_ready pending=%s", info.pending_update_count)
        else:
            self.master_dp_task = asyncio.create_task(self.master_dp.start_polling(self.master_bot, handle_signals=False))

    async def bootstrap_children(self) -> None:
        for doc in await self.repositories.bots.list_enabled():
            bot_id = int(doc["bot_id"])
            asyncio.create_task(self.start_bot(bot_id))
        self.supervisor_task = asyncio.create_task(self._supervisor_loop())
        self.room_cleanup_task = asyncio.create_task(self._room_cleanup_loop())

    async def register_bot(self, token: str, owner_id: int) -> BotInfo:
        async with self.lock:
            bot_id, username = await self.token_service.validate_token(token)
            if await self.repositories.bots.get(bot_id):
                raise ValueError("Ese bot_id ya está registrado")
            secret = self.token_service.new_webhook_secret()
            info_doc = {
                "bot_id": bot_id,
                "username": username,
                "owner_id": owner_id,
                "token_encrypted": self.token_service.encrypt(token),
                "webhook_secret_encrypted": self.token_service.encrypt(secret),
                "enabled": True,
                "status": BotStatus.CREATED.value,
                "config": {"features": {"media": True, "rooms": True, "webapp": True, "admin_feed": True}, "language": "es", "max_members_default": 100},
                "restart_count": 0,
                "last_error": None,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            await self.repositories.bots.create(info_doc)
        await self.start_bot(bot_id)
        stored = await self.repositories.bots.get(bot_id)
        info = BotInfo.from_document(stored or info_doc)
        await self.notify_admins_new_bot(info, owner_id)
        return info

    async def start_bot(self, bot_id: int) -> str:
        async with self.lock:
            if bot_id in self.registry:
                runtime = self.registry[bot_id]
                if runtime.status in {BotStatus.STARTING, BotStatus.RUNNING, BotStatus.RESTARTING}:
                    raise BotAlreadyRunningError(f"Bot {bot_id} ya está ejecutándose")
            doc = await self.repositories.bots.get(bot_id)
            if not doc:
                raise BotNotFoundError(str(bot_id))
            if not doc.get("enabled", True):
                raise ValueError("El bot está deshabilitado")
            info = BotInfo.from_document(doc)
            token = self.token_service.decrypt(info.encrypted_token)
            secret = self.token_service.decrypt(info.encrypted_webhook_secret)
            bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            dp = Dispatcher()
            runtime = BotRuntime(info=info, bot=bot, dispatcher=dp, status=BotStatus.STARTING)
            self.registry[bot_id] = runtime
            try:
                me = await bot.get_me()
                if int(me.id) != bot_id:
                    raise ValueError("El token ya no corresponde al bot almacenado")
                ctx = runtime.build_context(self.mongo.db, self.settings, None, self.repositories)
                from app.bot.child_factory import build_child_services
                build_child_services(ctx)
                dp.include_router(build_router(ctx))
                if self.settings.mode == "webhook":
                    url = f"{self.settings.webhook_base_url}/telegram/webhook/{bot_id}"
                    await bot.set_webhook(url=url, secret_token=secret, allowed_updates=dp.resolve_used_update_types(), max_connections=self.settings.telegram_max_connections, drop_pending_updates=self.settings.drop_pending_updates)
                    wh = await bot.get_webhook_info()
                    if wh.url != url:
                        raise RuntimeError("Webhook del bot hijo no coincide")
                    runtime.status = BotStatus.RUNNING
                else:
                    runtime.status = BotStatus.RUNNING
                    runtime.polling_task = runtime.add_task(dp.start_polling(bot, handle_signals=False))
                runtime.started_at = datetime.utcnow()
                runtime.restart_count = info.restart_count
                runtime.broadcast_queue = asyncio.Queue(maxsize=self.settings.room_queue_maxsize)
                runtime.album_queue = asyncio.Queue(maxsize=self.settings.room_queue_maxsize)
                runtime.admin_feed_queue = asyncio.Queue(maxsize=self.settings.room_queue_maxsize)
                for _ in range(self.settings.broadcast_workers_per_bot):
                    runtime.add_task(self._broadcast_worker(runtime))
                    runtime.add_task(self._album_worker(runtime))
                for _ in range(max(1, min(2, self.settings.broadcast_workers_per_bot))):
                    runtime.add_task(self._admin_feed_worker(runtime))
                await self.repositories.bots.mark_started(bot_id)
                runtime.add_task(self._heartbeat_loop(runtime))
                runtime.add_task(self._replay_pending_updates(bot_id))
                self.logger.info("child_bot_running bot_id=%s username=@%s", bot_id, me.username)
                return f"bot {bot_id} RUNNING"
            except Exception as exc:
                runtime.status = BotStatus.ERROR
                runtime.last_error = str(exc)[:1000]
                await self.repositories.bots.update_status(bot_id, BotStatus.ERROR.value, runtime.last_error)
                await bot.session.close()
                self.registry.pop(bot_id, None)
                self.logger.exception("child_bot_start_failed bot_id=%s", bot_id)
                raise

    async def stop_bot(self, bot_id: int) -> str:
        runtime = self.registry.get(bot_id)
        if not runtime:
            await self.repositories.bots.update_status(bot_id, BotStatus.STOPPED.value)
            return f"bot {bot_id} STOPPED"
        runtime.status = BotStatus.STOPPING
        if self.settings.mode == "webhook":
            try:
                await runtime.bot.delete_webhook(drop_pending_updates=False)
            except TelegramAPIError:
                self.logger.exception("delete_webhook_failed bot_id=%s", bot_id)
        await runtime.stop()
        self.registry.pop(bot_id, None)
        await self.repositories.bots.update_status(bot_id, BotStatus.STOPPED.value)
        return f"bot {bot_id} STOPPED"

    async def restart_bot(self, bot_id: int) -> str:
        now = time.monotonic()
        dq = self._failure_times[bot_id]
        dq.append(now)
        while dq and now - dq[0] > self.settings.restart_window_seconds:
            dq.popleft()
        if len(dq) > self.settings.max_restarts_per_window:
            await self.repositories.bots.update_status(bot_id, BotStatus.ERROR.value, "circuit breaker: demasiados reinicios")
            raise RuntimeError("Circuit breaker activado")
        await self.stop_bot(bot_id)
        delay = backoff_delay(len(dq) - 1, self.settings.max_restart_delay_seconds)
        await asyncio.sleep(delay)
        await self.repositories.bots.increment_restart(bot_id)
        return await self.start_bot(bot_id)

    async def bot_enable(self, bot_id: int) -> str:
        await self.repositories.bots.set_enabled(bot_id, True)
        return await self.start_bot(bot_id)

    async def bot_disable(self, bot_id: int) -> str:
        await self.repositories.bots.set_enabled(bot_id, False)
        return await self.stop_bot(bot_id)

    async def bot_delete(self, bot_id: int) -> str:
        await self.stop_bot(bot_id)
        await self.repositories.bots.delete(bot_id)
        return f"bot {bot_id} eliminado"

    async def get_info(self, bot_id: int) -> BotInfo:
        doc = await self.repositories.bots.get(bot_id)
        if not doc:
            raise BotNotFoundError(str(bot_id))
        return BotInfo.from_document(doc)

    def format_bot_info(self, info: BotInfo) -> str:
        runtime = self.registry.get(info.bot_id)
        queue = runtime.broadcast_queue.qsize() if runtime and runtime.broadcast_queue else 0
        return f"<b>{info.bot_id}</b> @{info.username}\nowner={info.owner_id}\nstatus={runtime.status if runtime else info.status}\nrestarts={info.restart_count}\nqueue={queue}\nheartbeat={info.last_heartbeat}"

    async def handle_webhook_update(self, bot_id: int, payload: dict) -> None:
        runtime = self.registry.get(bot_id)
        if not runtime:
            await self.start_bot(bot_id)
            runtime = self.registry.get(bot_id)
        if not runtime or runtime.status != BotStatus.RUNNING:
            return
        update_id = int(payload.get("update_id", 0))
        try:
            await runtime.dispatcher.feed_raw_update(runtime.bot, payload)
            runtime.metrics.inc("updates_total")
            if update_id:
                runtime.metrics.set("last_update_id", update_id)
                await self.repositories.media.mark_update(bot_id, update_id, "PROCESSED")
        except Exception:
            if update_id:
                await self.repositories.media.mark_update(bot_id, update_id, "FAILED")
            raise

    async def _replay_pending_updates(self, bot_id: int) -> None:
        pending = await self.repositories.media.pending_updates(bot_id, 500)
        if pending:
            self.logger.info("replaying_pending_updates bot_id=%s count=%s", bot_id, len(pending))
        for item in pending:
            try:
                await self.handle_webhook_update(bot_id, item["payload"])
            except Exception:
                self.logger.exception("pending_update_replay_failed bot_id=%s update_id=%s", bot_id, item.get("update_id"))

    async def _supervisor_loop(self) -> None:
        while not self.shutting_down:
            await asyncio.sleep(10)
            try:
                docs = await self.repositories.bots.list_enabled()
                now = datetime.utcnow()
                for doc in docs:
                    bot_id = int(doc["bot_id"])
                    runtime = self.registry.get(bot_id)
                    if runtime is None and doc.get("status") == BotStatus.RUNNING.value:
                        try:
                            await self.start_bot(bot_id)
                        except Exception:
                            self.logger.exception("supervisor_start_failed bot_id=%s", bot_id)
                        continue
                    if runtime and runtime.status == BotStatus.RUNNING:
                        heartbeat = doc.get("last_heartbeat")
                        if heartbeat and (now - heartbeat).total_seconds() > self.settings.heartbeat_interval_seconds * 3:
                            self.logger.warning("stale_heartbeat bot_id=%s", bot_id)
                            try:
                                await self.restart_bot(bot_id)
                            except Exception:
                                self.logger.exception("supervisor_restart_failed bot_id=%s", bot_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("supervisor_loop_error")

    async def _broadcast_worker(self, runtime: BotRuntime) -> None:
        assert runtime.broadcast_queue is not None
        while not runtime.stop_event.is_set():
            job = await runtime.broadcast_queue.get()
            try:
                await runtime.ctx.services.broadcast.process_single(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                runtime.logger.exception("broadcast_worker_failed bot_id=%s", runtime.info.bot_id)
            finally:
                runtime.broadcast_queue.task_done()

    async def _album_worker(self, runtime: BotRuntime) -> None:
        assert runtime.album_queue is not None
        while not runtime.stop_event.is_set():
            job = await runtime.album_queue.get()
            try:
                await runtime.ctx.services.broadcast.process_album(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                runtime.logger.exception("album_worker_failed bot_id=%s", runtime.info.bot_id)
            finally:
                runtime.album_queue.task_done()

    async def _admin_feed_worker(self, runtime: BotRuntime) -> None:
        assert runtime.admin_feed_queue is not None
        while not runtime.stop_event.is_set():
            job = await runtime.admin_feed_queue.get()
            try:
                await runtime.ctx.services.admin_feed.process(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                runtime.logger.exception("admin_feed_worker_failed bot_id=%s", runtime.info.bot_id)
            finally:
                runtime.admin_feed_queue.task_done()

    async def notify_admins_new_bot(self, info: BotInfo, creator_id: int) -> None:
        name = f"@{info.username}" if info.username else str(info.bot_id)
        text = (
            "<b>🚀 NUEVO BOT REGISTRADO</b>\n\n"
            f"🤖 <b>{name}</b>\n"
            f"🆔 <code>{info.bot_id}</code>\n"
            f"👤 Creador: <code>{creator_id}</code>\n"
            "🟢 Estado: <b>RUNNING</b>\n\n"
            "El bot hijo fue validado, configurado y activado correctamente."
        )
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        if info.username:
            rows.append([InlineKeyboardButton(text="🤖 Abrir bot", url=f"https://t.me/{info.username}")])
        rows.append([
            InlineKeyboardButton(text="📊 Ver estado", callback_data=f"master:botinfo:{info.bot_id}"),
            InlineKeyboardButton(text="🔄 Reiniciar", callback_data=f"master:restart:{info.bot_id}"),
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        for admin_id in self.settings.admin_ids:
            try:
                await self.master_bot.send_message(admin_id, text, reply_markup=markup)
            except Exception:
                self.logger.exception("admin_notification_failed admin_id=%s bot_id=%s", admin_id, info.bot_id)

    async def _room_cleanup_loop(self) -> None:
        while not self.shutting_down:
            await asyncio.sleep(60)
            try:
                expired = await self.repositories.room.expire_due()
                if expired:
                    self.logger.info("rooms_expired count=%s", expired)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("room_cleanup_loop_error")

    async def _heartbeat_loop(self, runtime: BotRuntime) -> None:
        while not self.shutting_down and runtime.status == BotStatus.RUNNING:
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)
            await self.repositories.bots.heartbeat(runtime.info.bot_id)
            runtime.metrics.set("runtime_tasks", runtime.task_registry.count if runtime.task_registry else 0)

    async def shutdown(self) -> None:
        self.shutting_down = True
        if self.supervisor_task:
            self.supervisor_task.cancel()
            await asyncio.gather(self.supervisor_task, return_exceptions=True)
        if self.room_cleanup_task:
            self.room_cleanup_task.cancel()
            await asyncio.gather(self.room_cleanup_task, return_exceptions=True)
        bots = list(self.registry)
        for bot_id in bots:
            try:
                await self.stop_bot(bot_id)
            except Exception:
                self.logger.exception("child_shutdown_failed bot_id=%s", bot_id)
        if self.settings.mode == "webhook":
            try:
                await self.master_bot.delete_webhook(drop_pending_updates=False)
            except TelegramAPIError:
                self.logger.exception("master_delete_webhook_failed")
        if hasattr(self, "master_dp_task"):
            self.master_dp_task.cancel()
            await asyncio.gather(self.master_dp_task, return_exceptions=True)
        await self.master_bot.session.close()
