from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo

from app.bot.child_handlers import build_router as build_child_router
from app.bot.child_factory import build_child_services
from app.bot.runtime import BotRuntime
from app.core.datetime import age_seconds, utcnow
from app.core.enums import BotStatus
from app.core.models import BotInfo
from app.core.tasks import TaskRegistry
from app.services.child_bot_provisioner import ChildBotProvisioner
from app.services.token_service import TokenService
from app.services.bot_lifecycle import botfather_instructions, schedule_label


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
        self.provisioner = ChildBotProvisioner(self)
        self.registry: dict[int, BotRuntime] = {}
        self.logger = logging.getLogger("bot.manager")
        self.lock = asyncio.Lock()
        self.system_tasks = TaskRegistry("system")
        self.restart_history: dict[int, deque[float]] = defaultdict(deque)
        self.shutting_down = False
        self.ready = False
        # Avoid webhook-repair storms when another process is competing for the same token.
        self._webhook_repair_last: dict[str, float] = {}
        self._webhook_repair_history: dict[str, deque[float]] = defaultdict(deque)
        self.master_bot = Bot(settings.master_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.master_dp = Dispatcher()

    async def start(self) -> None:
        await self._start_master()
        await self._bootstrap_children()
        self.ready = True
        self.logger.info("platform_ready master=%s children=%s", self.master_bot.id if getattr(self.master_bot, "id", None) else "ready", len(self.registry))
        self.system_tasks.create(self._supervisor_loop(), "supervisor")
        self.system_tasks.create(self._room_cleanup_loop(), "room-cleanup")
        self.system_tasks.create(self._bot_lifecycle_loop(), "bot-lifecycle")

    async def _start_master(self) -> None:
        me = await self.master_bot.get_me()
        self.master_bot_username = me.username or ""
        self.logger.info("master_ready username=@%s id=%s", me.username, me.id)
        from app.bot.master_handlers import build_master_router
        self.master_dp.include_router(build_master_router(self))
        await self._configure_menu_button(
            self.master_bot,
            f"{self.settings.app_base_url}/master-app",
            "🚀 Master App",
            label="master",
        )
        if self.settings.environment == "production":
            await self.configure_webhook(self.master_bot, self.settings.webhook_base_url + self.settings.master_webhook_path, self.settings.webhook_secret, "master", self.master_dp)
        elif self.settings.mode == "webhook":
            await self.configure_webhook(self.master_bot, self.settings.webhook_base_url + self.settings.master_webhook_path, self.settings.webhook_secret, "master", self.master_dp)
        else:
            self.system_tasks.create(self.master_dp.start_polling(self.master_bot, handle_signals=False), "master-polling")

    async def _configure_menu_button(self, bot: Bot, url: str, text: str, label: str) -> None:
        try:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text=text, web_app=WebAppInfo(url=url)))
            self.logger.info("mini_app_menu_ready label=%s url=%s", label, url)
        except Exception:
            self.logger.exception("mini_app_menu_failed label=%s", label)

    async def configure_webhook(self, bot: Bot, url: str, secret: str, label: str, dispatcher: Dispatcher) -> None:
        allowed = dispatcher.resolve_used_update_types() or ["message", "callback_query"]
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                self.logger.info("webhook_configuring label=%s attempt=%s/3 url=%s allowed=%s", label, attempt, url, allowed)
                await bot.set_webhook(
                    url=url,
                    secret_token=secret,
                    allowed_updates=allowed,
                    max_connections=self.settings.telegram_max_connections,
                    drop_pending_updates=self.settings.drop_pending_updates,
                )
                info = await bot.get_webhook_info()
                if info.url != url:
                    raise RuntimeError(f"Telegram devolvió webhook distinto: {info.url!r}")
                if info.last_error_message:
                    self.logger.warning("webhook_last_error label=%s error=%s date=%s", label, info.last_error_message, info.last_error_date)
                self.logger.info("webhook_ready label=%s pending=%s last_error=%s", label, info.pending_update_count, info.last_error_message)
                return
            except Exception as exc:
                last_error = exc
                self.logger.warning("webhook_config_failed label=%s attempt=%s error=%s", label, attempt, exc)
                if attempt < 3:
                    await asyncio.sleep(attempt * 1.5)
        raise RuntimeError(f"No se pudo configurar el webhook de {label}: {last_error}")

    async def _bootstrap_children(self) -> None:
        docs = await self.repositories.bots.list_enabled()
        failures = 0
        for doc in docs:
            bot_id = int(doc["bot_id"])
            try:
                await self.start_bot(bot_id)
            except TelegramUnauthorizedError:
                failures += 1
                await self._mark_auth_error(bot_id, "Telegram rechazó el token almacenado")
                self.logger.error("child_bootstrap_auth_error bot_id=%s", bot_id)
            except Exception:
                failures += 1
                self.logger.exception("child_bootstrap_failed bot_id=%s", bot_id)
        self.logger.info("children_bootstrap_complete total=%s running=%s failures=%s", len(docs), len(self.registry), failures)

    async def register_bot(self, token: str, owner_id: int, metadata: dict | None = None) -> BotInfo:
        return await self.provisioner.create(token, owner_id, metadata)

    async def delete_bot(self, bot_id: int) -> str:
        """Legacy entry point: archive the runtime without deleting persisted content."""
        await self.archive_bot(bot_id, reason="manual")
        return f"bot {bot_id} archivado"

    async def schedule_bot_expiration(self, bot_id: int, minutes: int, actor_id: int) -> str:
        if minutes <= 0:
            await self.repositories.bots.clear_lifecycle(bot_id)
            return "Temporizador desactivado. El bot queda en modo manual."
        info = await self.get_info(bot_id)
        now = utcnow()
        delete_at = now + timedelta(minutes=int(minutes))
        await self.repositories.bots.set_lifecycle(bot_id, {
            "mode": "TIMER",
            "delete_at": delete_at,
            "scheduled_at": now,
            "scheduled_by": int(actor_id),
            "label": schedule_label(int(minutes)),
        })
        return f"Temporizador configurado: {schedule_label(int(minutes))}. Vence {delete_at.isoformat()}"

    async def cancel_bot_expiration(self, bot_id: int) -> str:
        await self.repositories.bots.clear_lifecycle(bot_id)
        return "Temporizador cancelado. El bot queda en modo manual."

    async def _notify_owner(self, info: BotInfo, *, reason: str, expires_at=None) -> None:
        text, markup = botfather_instructions(info.username, reason=reason, expires_at=expires_at)
        runtime = self.registry.get(info.bot_id)
        if runtime and runtime.status == BotStatus.RUNNING:
            try:
                await runtime.bot.send_message(info.owner_id, text, reply_markup=markup)
            except Exception:
                self.logger.info("owner_child_notification_unavailable bot_id=%s owner_id=%s", info.bot_id, info.owner_id)
        try:
            await self.master_bot.send_message(info.owner_id, text, reply_markup=markup)
        except Exception:
            self.logger.info("owner_master_notification_unavailable bot_id=%s owner_id=%s", info.bot_id, info.owner_id)

    async def archive_bot(self, bot_id: int, reason: str = "manual") -> None:
        info = await self.get_info(bot_id)
        lifecycle = (await self.repositories.bots.get(bot_id) or {}).get("config", {}).get("lifecycle", {})
        expires_at = lifecycle.get("delete_at")
        await self._notify_owner(info, reason=reason, expires_at=expires_at)
        await self.stop_bot(bot_id)
        await self.repositories.bots.archive(bot_id, reason)
        self.logger.info("bot_archived bot_id=%s reason=%s", bot_id, reason)

    async def notify_creator_new_bot(self, info: BotInfo, creator_id: int) -> None:
        text = (
            "<b>✅ TU BOT HIJO YA ESTÁ ACTIVO</b>\n\n"
            f"🤖 <b>@{info.username or info.bot_id}</b>\n"
            "🟢 Webhook y runtime activos.\n"
            "📱 Abre el bot y pulsa <b>📱 Abrir Mini App</b> para administrar sus salas.\n\n"
            "⏱️ El bot queda en modo <b>Manual</b> hasta que configures un temporizador.\n"
            "🗑️ Para eliminarlo definitivamente de Telegram tendrás que hacerlo desde @BotFather."
        )
        rows = []
        if info.username:
            rows.append([InlineKeyboardButton(text="🤖 Abrir bot", url=f"https://t.me/{info.username}")])
        rows.append([InlineKeyboardButton(text="🛠️ Gestionar bot", callback_data=f"master:botinfo:{info.bot_id}")])
        try:
            await self.master_bot.send_message(creator_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except Exception:
            self.logger.info("creator_master_notification_unavailable bot_id=%s creator_id=%s", info.bot_id, creator_id)
        runtime = self.registry.get(info.bot_id)
        if runtime:
            try:
                await runtime.bot.send_message(creator_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
                await self.repositories.bots.update_config(info.bot_id, {"owner_onboarding_sent": True})
            except Exception:
                self.logger.info("creator_child_notification_pending_start bot_id=%s creator_id=%s", info.bot_id, creator_id)

    async def get_info(self, bot_id: int) -> BotInfo:
        doc = await self.repositories.bots.get(bot_id)
        if not doc:
            raise ValueError("Bot no encontrado")
        return BotInfo.from_document(doc)

    async def _mark_auth_error(self, bot_id: int, error: str) -> None:
        runtime = self.registry.get(int(bot_id))
        if runtime:
            runtime.last_error = error
            try:
                await runtime.stop()
            except Exception:
                self.logger.exception("auth_error_runtime_stop_failed bot_id=%s", bot_id)
            self.registry.pop(int(bot_id), None)
        await self.repositories.bots.mark_auth_error(int(bot_id), error)

    async def replace_bot_token(self, bot_id: int, token: str, actor_id: int) -> BotInfo:
        info = await self.get_info(bot_id)
        if int(actor_id) != info.owner_id and int(actor_id) not in self.settings.admin_ids:
            raise PermissionError("No autorizado para actualizar el token de este bot")

        new_bot_id, username, first_name = await self.token_service.validate_token(token)
        if int(new_bot_id) != int(bot_id):
            raise ValueError("El token enviado pertenece a otro bot. Debes usar el token del mismo bot registrado.")

        if bot_id in self.registry:
            try:
                await self.stop_bot(bot_id)
            except Exception:
                self.logger.exception("token_rotation_stop_failed bot_id=%s", bot_id)

        await self.repositories.bots.replace_token(
            bot_id,
            self.token_service.encrypt(token.strip()),
            username,
            first_name,
        )
        result = await self.start_bot(bot_id)
        self.logger.info("child_token_rotated bot_id=%s actor_id=%s username=@%s result=%s", bot_id, actor_id, username, result)
        return await self.get_info(bot_id)

    async def start_bot(self, bot_id: int) -> str:
        async with self.lock:
            if bot_id in self.registry and self.registry[bot_id].status in {BotStatus.STARTING, BotStatus.RUNNING, BotStatus.RESTARTING}:
                return f"bot {bot_id} already running"

            doc = await self.repositories.bots.get(bot_id)
            if not doc:
                raise ValueError(f"Bot {bot_id} no existe")
            if not bool(doc.get("enabled", True)):
                raise ValueError("El bot está deshabilitado")

            info = BotInfo.from_document(doc)
            token = self.token_service.decrypt(info.encrypted_token)
            secret = self.token_service.decrypt(info.encrypted_webhook_secret)
            bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            dispatcher = Dispatcher()
            runtime = BotRuntime(info=info, bot=bot, dispatcher=dispatcher, status=BotStatus.STARTING)
            runtime.broadcast_queue = asyncio.Queue(maxsize=self.settings.room_queue_maxsize)
            runtime.album_queue = asyncio.Queue(maxsize=self.settings.room_queue_maxsize)
            runtime.admin_feed_queue = asyncio.Queue(maxsize=self.settings.room_queue_maxsize)
            self.registry[bot_id] = runtime
            await self.repositories.bots.mark_starting(bot_id)

            try:
                me = await bot.get_me()
                if int(me.id) != bot_id:
                    raise RuntimeError("El token almacenado ya no corresponde con el bot registrado")
                runtime.info.username = me.username
                runtime.ctx = runtime.build_context(self.mongo.db, self.settings, self.repositories, None)
                build_child_services(runtime.ctx)
                dispatcher.include_router(build_child_router(runtime.ctx))

                if self.settings.environment == "production" or self.settings.mode == "webhook":
                    url = f"{self.settings.webhook_base_url}/telegram/webhook/{bot_id}"
                    await self.configure_webhook(bot, url, secret, f"child:{bot_id}", dispatcher)
                else:
                    runtime.add_task(dispatcher.start_polling(bot, handle_signals=False), f"child-polling:{bot_id}")

                await self._configure_menu_button(
                    bot,
                    f"{self.settings.app_base_url}/app?bot_id={bot_id}",
                    "📱 Mini App",
                    label=f"child:{bot_id}",
                )

                runtime.status = BotStatus.RUNNING
                runtime.started_at = utcnow()
                runtime.restart_count = info.restart_count
                await self.repositories.bots.mark_running(bot_id)

                for index in range(self.settings.broadcast_workers_per_bot):
                    runtime.add_task(self._broadcast_worker(runtime), f"broadcast:{bot_id}:{index}")
                runtime.add_task(self._album_worker(runtime), f"album:{bot_id}")
                runtime.add_task(self._admin_feed_worker(runtime), f"admin-feed:{bot_id}")
                runtime.add_task(self._heartbeat_loop(runtime), f"heartbeat:{bot_id}")
                runtime.add_task(self._replay_pending_updates(bot_id), f"replay:{bot_id}")
                self.logger.info("child_bot_running bot_id=%s username=@%s", bot_id, me.username)
                return f"bot {bot_id} RUNNING"
            except TelegramUnauthorizedError:
                runtime.status = BotStatus.AUTH_ERROR
                runtime.last_error = "Telegram rechazó el token del bot"
                await runtime.stop()
                self.registry.pop(bot_id, None)
                await self.repositories.bots.mark_auth_error(bot_id, runtime.last_error)
                raise
            except Exception as exc:
                runtime.status = BotStatus.ERROR
                runtime.last_error = str(exc)[:1000]
                await self.repositories.bots.update_status(bot_id, BotStatus.ERROR.value, runtime.last_error)
                await runtime.stop()
                self.registry.pop(bot_id, None)
                raise

    async def stop_bot(self, bot_id: int) -> str:
        runtime = self.registry.get(bot_id)
        if not runtime:
            await self.repositories.bots.update_status(bot_id, BotStatus.STOPPED.value)
            return f"bot {bot_id} STOPPED"
        runtime.status = BotStatus.STOPPING
        if self.settings.environment == "production" or self.settings.mode == "webhook":
            try:
                await runtime.bot.delete_webhook(drop_pending_updates=False)
            except TelegramAPIError:
                self.logger.exception("child_delete_webhook_failed bot_id=%s", bot_id)
        await runtime.stop()
        self.registry.pop(bot_id, None)
        await self.repositories.bots.update_status(bot_id, BotStatus.STOPPED.value)
        return f"bot {bot_id} STOPPED"

    async def restart_bot(self, bot_id: int, reason: str = "manual") -> str:
        now = time.monotonic()
        history = self.restart_history[bot_id]
        history.append(now)
        while history and now - history[0] > self.settings.restart_window_seconds:
            history.popleft()
        if len(history) > self.settings.max_restarts_per_window:
            await self.repositories.bots.update_status(bot_id, BotStatus.ERROR.value, "Circuit breaker: demasiados reinicios")
            raise RuntimeError("Circuit breaker activado para este bot")
        await self.repositories.bots.update_status(bot_id, BotStatus.RESTARTING.value, f"restart:{reason}")
        await self.stop_bot(bot_id)
        await asyncio.sleep(min(self.settings.max_restart_delay_seconds, max(1, len(history) - 1)))
        await self.repositories.bots.increment_restart(bot_id)
        return await self.start_bot(bot_id)

    async def handle_master_webhook_update(self, payload: dict) -> None:
        if not self.ready:
            raise RuntimeError("Master todavía no está listo")
        update_id = payload.get("update_id")
        self.logger.info("master_webhook_dispatch update_id=%s", update_id)
        result = await self.master_dp.feed_raw_update(self.master_bot, payload)
        self.logger.info("master_dispatch_result update_id=%s result_type=%s", update_id, type(result).__name__)

    async def handle_webhook_update(self, bot_id: int, payload: dict) -> None:
        runtime = self.registry.get(bot_id)
        if runtime is None:
            try:
                await self.start_bot(bot_id)
            except Exception:
                self.logger.exception("webhook_runtime_start_failed bot_id=%s", bot_id)
                raise
            runtime = self.registry.get(bot_id)
        if not runtime or runtime.status != BotStatus.RUNNING:
            raise RuntimeError(f"Bot {bot_id} no está RUNNING")
        update_id = payload.get("update_id")
        self.logger.info("webhook_dispatch bot_id=%s update_id=%s", bot_id, update_id)
        try:
            result = await runtime.dispatcher.feed_raw_update(runtime.bot, payload)
            runtime.last_update_at = utcnow()
            self.logger.info("webhook_dispatch_result bot_id=%s update_id=%s result_type=%s", bot_id, update_id, type(result).__name__)
            if update_id is not None:
                await self.repositories.media.mark_update(bot_id, int(update_id), "PROCESSED")
        except TelegramBadRequest as exc:
            # Callback queries expire quickly. This can happen after a Render restart
            # when a persisted callback update is replayed, or after a delayed delivery.
            message = str(exc).lower()
            if "query is too old" in message or "query id is invalid" in message or "response timeout expired" in message:
                if update_id is not None:
                    await self.repositories.media.mark_update(bot_id, int(update_id), "SKIPPED")
                runtime.last_update_at = utcnow()
                self.logger.warning(
                    "stale_callback_ignored bot_id=%s update_id=%s error=%s",
                    bot_id, update_id, exc,
                )
                return
            if update_id is not None:
                await self.repositories.media.mark_update(bot_id, int(update_id), "FAILED")
            self.logger.exception("webhook_dispatch_failed bot_id=%s update_id=%s", bot_id, update_id)
            raise
        except Exception:
            if update_id is not None:
                await self.repositories.media.mark_update(bot_id, int(update_id), "FAILED")
            self.logger.exception("webhook_dispatch_failed bot_id=%s update_id=%s", bot_id, update_id)
            raise

    async def _replay_pending_updates(self, bot_id: int) -> None:
        pending = await self.repositories.media.pending_updates(bot_id, 200)
        if not pending:
            return
        self.logger.info("replaying_pending_updates bot_id=%s count=%s", bot_id, len(pending))
        for item in pending:
            payload = item.get("payload") or {}
            update_id = item.get("update_id")
            # CallbackQuery IDs are short-lived and cannot be replayed after a
            # restart. The user can simply press the button again. Replaying them
            # only produces QUERY_ID_INVALID / "query is too old" errors.
            if payload.get("callback_query"):
                if update_id is not None:
                    await self.repositories.media.mark_update(bot_id, int(update_id), "SKIPPED")
                self.logger.info("stale_callback_replay_skipped bot_id=%s update_id=%s", bot_id, update_id)
                continue
            try:
                await self.handle_webhook_update(bot_id, payload)
            except Exception:
                self.logger.exception("pending_update_replay_failed bot_id=%s update_id=%s", bot_id, update_id)

    async def _broadcast_worker(self, runtime: BotRuntime) -> None:
        assert runtime.broadcast_queue is not None
        while not runtime.stop_event.is_set():
            job = await runtime.broadcast_queue.get()
            try:
                await runtime.ctx.services.broadcast.process_single(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                runtime.logger.exception("broadcast_worker_failed")
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
                runtime.logger.exception("album_worker_failed")
            finally:
                runtime.album_queue.task_done()

    async def _admin_feed_worker(self, runtime: BotRuntime) -> None:
        assert runtime.admin_feed_queue is not None
        while not runtime.stop_event.is_set():
            item = await runtime.admin_feed_queue.get()
            try:
                await runtime.ctx.services.admin_feed.process(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                runtime.logger.exception("admin_feed_worker_failed")
            finally:
                runtime.admin_feed_queue.task_done()

    async def notify_admins_new_bot(self, info: BotInfo, creator_id: int) -> None:
        text = (
            "<b>🚀 NUEVO BOT REGISTRADO</b>\n\n"
            f"🤖 @{info.username or info.bot_id}\n"
            f"🆔 <code>{info.bot_id}</code>\n"
            f"👤 Creador: <code>{creator_id}</code>\n"
            "🟢 Estado: <b>RUNNING</b>"
        )
        rows = []
        if info.username:
            rows.append([InlineKeyboardButton(text="🤖 Abrir bot", url=f"https://t.me/{info.username}")])
        rows.append([
            InlineKeyboardButton(text="📊 Estado", callback_data=f"master:botinfo:{info.bot_id}"),
            InlineKeyboardButton(text="🔄 Reiniciar", callback_data=f"master:restart:{info.bot_id}"),
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        for admin_id in self.settings.admin_ids:
            try:
                await self.master_bot.send_message(admin_id, text, reply_markup=markup)
            except Exception:
                self.logger.exception("admin_notification_failed admin_id=%s", admin_id)

    async def _heartbeat_loop(self, runtime: BotRuntime) -> None:
        while not self.shutting_down and runtime.status == BotStatus.RUNNING:
            try:
                await asyncio.sleep(self.settings.heartbeat_interval_seconds)
                if runtime.status != BotStatus.RUNNING:
                    break
                await self.repositories.bots.heartbeat(runtime.info.bot_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                runtime.logger.exception("heartbeat_failed")

    async def _webhook_repair_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cooldown = max(30, int(self.settings.supervisor_interval_seconds * 4))
        last = self._webhook_repair_last.get(key, 0.0)
        if now - last < cooldown:
            return False
        self._webhook_repair_last[key] = now
        history = self._webhook_repair_history[key]
        history.append(now)
        while history and now - history[0] > 600:
            history.popleft()
        if len(history) >= 3:
            self.logger.critical("webhook_conflict_suspected key=%s repairs_last_10m=%s; verify no second process/polling instance is using this bot token", key, len(history))
        return True

    async def _supervisor_loop(self) -> None:
        """Keep the Master and child runtimes operational without falling back to polling in production."""
        while not self.shutting_down:
            await asyncio.sleep(self.settings.supervisor_interval_seconds)
            if self.shutting_down:
                break
            try:
                # Master health: Telegram session + effective webhook.
                try:
                    await self.master_bot.get_me()
                    if self.settings.environment == "production" or self.settings.mode == "webhook":
                        expected_master_url = self.settings.webhook_base_url + self.settings.master_webhook_path
                        info = await self.master_bot.get_webhook_info()
                        if info.url != expected_master_url and await self._webhook_repair_allowed("master"):
                            self.logger.warning(
                                "master_webhook_repair url=%s expected=%s last_error=%s",
                                info.url, expected_master_url, info.last_error_message,
                            )
                            await self.configure_webhook(
                                self.master_bot,
                                expected_master_url,
                                self.settings.webhook_secret,
                                "master",
                                self.master_dp,
                            )
                except TelegramUnauthorizedError:
                    self.logger.critical("master_token_rejected_by_telegram")
                except Exception:
                    self.logger.exception("master_supervisor_check_failed")

                docs = await self.repositories.bots.list_enabled()
                now = utcnow()
                expected_long_lived_tasks = self.settings.broadcast_workers_per_bot + 3  # broadcast workers + album + admin feed + heartbeat

                for doc in docs:
                    bot_id = int(doc["bot_id"])
                    runtime = self.registry.get(bot_id)
                    if runtime is None:
                        if doc.get("status") in {BotStatus.RUNNING.value, BotStatus.STARTING.value, BotStatus.RESTARTING.value}:
                            try:
                                await self.start_bot(bot_id)
                            except Exception:
                                self.logger.exception("supervisor_start_failed bot_id=%s", bot_id)
                        continue

                    if runtime.status != BotStatus.RUNNING:
                        continue

                    heartbeat = doc.get("last_heartbeat")
                    age = age_seconds(heartbeat, now)
                    if age is not None and age > self.settings.heartbeat_interval_seconds * self.settings.heartbeat_grace_multiplier:
                        self.logger.warning("supervisor_stale_heartbeat bot_id=%s age=%.1fs", bot_id, age)
                        try:
                            await self.restart_bot(bot_id, reason="stale_heartbeat")
                            continue
                        except Exception:
                            self.logger.exception("supervisor_restart_failed bot_id=%s", bot_id)

                    if runtime.task_registry and runtime.task_registry.count < expected_long_lived_tasks:
                        self.logger.warning(
                            "supervisor_missing_tasks bot_id=%s expected>=%s actual=%s",
                            bot_id, expected_long_lived_tasks, runtime.task_registry.count,
                        )
                        try:
                            await self.restart_bot(bot_id, reason="missing_runtime_tasks")
                            continue
                        except Exception:
                            self.logger.exception("supervisor_task_restart_failed bot_id=%s", bot_id)

                    try:
                        await runtime.bot.get_me()
                        if self.settings.environment == "production" or self.settings.mode == "webhook":
                            secret = self.token_service.decrypt(runtime.info.encrypted_webhook_secret)
                            expected_url = f"{self.settings.webhook_base_url}/telegram/webhook/{bot_id}"
                            wh = await runtime.bot.get_webhook_info()
                            if wh.url != expected_url and await self._webhook_repair_allowed(f"child:{bot_id}"):
                                self.logger.warning(
                                    "child_webhook_repair bot_id=%s url=%s expected=%s last_error=%s",
                                    bot_id, wh.url, expected_url, wh.last_error_message,
                                )
                                await self.configure_webhook(runtime.bot, expected_url, secret, f"child:{bot_id}", runtime.dispatcher)
                                await self._configure_menu_button(
                                    runtime.bot,
                                    f"{self.settings.app_base_url}/app?bot_id={bot_id}",
                                    "📱 Mini App",
                                    label=f"child:{bot_id}",
                                )
                    except TelegramUnauthorizedError:
                        self.logger.error("child_token_revoked bot_id=%s", bot_id)
                        await self._mark_auth_error(bot_id, "Telegram rechazó el token del bot")
                    except TelegramAPIError:
                        self.logger.exception("child_telegram_health_check_failed bot_id=%s", bot_id)
                    except Exception:
                        self.logger.exception("child_supervisor_check_failed bot_id=%s", bot_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("supervisor_loop_error")

    async def _bot_lifecycle_loop(self) -> None:
        while not self.shutting_down:
            try:
                await asyncio.sleep(min(self.settings.supervisor_interval_seconds, 30))
                now = utcnow()
                docs = await self.repositories.bots.list_all(500)
                for doc in docs:
                    if not doc.get("enabled", True):
                        continue
                    lifecycle = (doc.get("config") or {}).get("lifecycle") or {}
                    if lifecycle.get("mode") != "TIMER" or not lifecycle.get("delete_at"):
                        continue
                    delete_at = lifecycle.get("delete_at")
                    age = age_seconds(delete_at, now)
                    if age is None or age < 0:
                        continue
                    bot_id = int(doc["bot_id"])
                    try:
                        await self.archive_bot(bot_id, reason="timer")
                    except Exception:
                        self.logger.exception("bot_expiration_failed bot_id=%s", bot_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("bot_lifecycle_loop_error")

    async def _room_cleanup_loop(self) -> None:
        while not self.shutting_down:
            try:
                await asyncio.sleep(60)
                changed = await self.repositories.room.expire_due()
                if changed:
                    self.logger.info("rooms_expired count=%s", changed)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("room_cleanup_failed")

    async def shutdown(self) -> None:
        self.shutting_down = True
        self.ready = False
        await self.system_tasks.cancel_all()
        for bot_id in list(self.registry.keys()):
            try:
                await self.stop_bot(bot_id)
            except Exception:
                self.logger.exception("child_shutdown_failed bot_id=%s", bot_id)
        try:
            if self.settings.environment == "production" or self.settings.mode == "webhook":
                await self.master_bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            self.logger.exception("master_delete_webhook_failed")
        try:
            await self.master_bot.session.close()
        except Exception:
            self.logger.exception("master_session_close_failed")

    async def get_health(self) -> dict:
        running = sum(1 for runtime in self.registry.values() if runtime.status == BotStatus.RUNNING)
        errors = sum(1 for runtime in self.registry.values() if runtime.status == BotStatus.ERROR)
        return {"ready": self.ready, "children": len(self.registry), "children_running": running, "children_error": errors}
