from __future__ import annotations

import asyncio
import logging


class TaskRegistry:
    def __init__(self, logger: logging.Logger) -> None:
        self._tasks: set[asyncio.Task[object]] = set()
        self._logger = logger

    def add(self, task: asyncio.Task[object]) -> asyncio.Task[object]:
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc:
            self._logger.exception("background_task_failed", exc_info=exc)

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
