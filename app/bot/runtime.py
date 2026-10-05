from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.context import BotContext
from app.core.enums import BotStatus
from app.core.models import BotInfo
from app.core.tasks import TaskRegistry


@dataclass(slots=True)
class BotRuntime:
    info: BotInfo
    bot: Any
    dispatcher: Any
    status: BotStatus
    ctx: BotContext | None = None
    task_registry: TaskRegistry | None = None
    broadcast_queue: asyncio.Queue | None = None
    album_queue: asyncio.Queue | None = None
    admin_feed_queue: asyncio.Queue | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: Any = None
    last_update_at: Any = None
    last_error: str | None = None
    restart_count: int = 0
    logger: logging.Logger = field(init=False)

    def __post_init__(self) -> None:
        self.task_registry = TaskRegistry(f"bot-{self.info.bot_id}")
        self.logger = logging.getLogger(f"bot.runtime.{self.info.bot_id}")

    def build_context(self, db, settings, repositories, services) -> BotContext:
        self.ctx = BotContext(
            bot_id=self.info.bot_id,
            bot_username=self.info.username,
            owner_id=self.info.owner_id,
            bot=self.bot,
            db=db,
            settings=settings,
            repositories=repositories,
            services=services,
            logger=self.logger,
            broadcast_queue=self.broadcast_queue,
            album_queue=self.album_queue,
            admin_feed_queue=self.admin_feed_queue,
            task_registry=self.task_registry,
        )
        return self.ctx

    def add_task(self, coro, task_name: str | None = None):
        if not self.task_registry:
            raise RuntimeError("TaskRegistry no inicializado")
        return self.task_registry.create(coro, task_name=task_name)

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task_registry:
            await self.task_registry.cancel_all()
        try:
            await self.bot.session.close()
        except Exception:
            self.logger.exception("bot_session_close_failed")
