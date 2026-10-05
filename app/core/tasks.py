from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any


class TaskRegistry:
    def __init__(self, name: str) -> None:
        self.name = name
        self._tasks: set[asyncio.Task[Any]] = set()
        self.logger = logging.getLogger(f"tasks.{name}")

    def create(self, coro: Coroutine[Any, Any, Any], task_name: str | None = None) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=task_name or f"{self.name}-task")
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception:
            self.logger.exception("task_exception_read_failed")
            return
        if exc:
            self.logger.error(
                "task_failed name=%s error=%s",
                task.get_name(),
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def cancel_all(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @property
    def count(self) -> int:
        return len(self._tasks)
