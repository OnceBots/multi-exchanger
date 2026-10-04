from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, Dispatcher

from app.core.context import BotContext
from app.core.enums import BotStatus
from app.core.metrics import Metrics
from app.core.task_registry import TaskRegistry


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BotRuntime:
    info: Any
    bot: Bot
    dispatcher: Dispatcher
    ctx: BotContext | None = None
    status: BotStatus = BotStatus.CREATED
    started_at: datetime | None = None
    last_error: str | None = None
    restart_count: int = 0
    task_registry: TaskRegistry | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    broadcast_queue: asyncio.Queue[dict] | None = None
    album_queue: asyncio.Queue[dict] | None = None
    admin_feed_queue: asyncio.Queue[dict] | None = None
    metrics: Metrics = field(default_factory=Metrics)
    restart_history: list[float] = field(default_factory=list)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("bot.runtime"))
    worker_tasks: list[asyncio.Task[object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.task_registry = TaskRegistry(self.logger)

    def build_context(self, db, settings, services, repositories) -> BotContext:
        self.ctx = BotContext(
            bot_id=self.info.bot_id,
            bot=self.bot,
            dispatcher=self.dispatcher,
            db=db,
            settings=settings,
            runtime=self,
            logger=logging.getLogger(f"child.{self.info.bot_id}"),
            services=services,
            repositories=repositories,
            config=self.info.config,
        )
        self.logger = self.ctx.logger
        self.task_registry = TaskRegistry(self.logger)
        return self.ctx

    def add_task(self, coro) -> asyncio.Task[object]:
        task = asyncio.create_task(coro)
        assert self.task_registry is not None
        return self.task_registry.add(task)

    async def stop(self) -> None:
        self.stop_event.set()
        self.status = BotStatus.STOPPING
        if self.task_registry:
            await self.task_registry.cancel_all()
        await self.bot.session.close()
        self.status = BotStatus.STOPPED
