from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class BotContext:
    bot_id: int
    bot_username: str | None
    owner_id: int
    bot: Any
    db: Any
    settings: Any
    repositories: Any
    services: Any
    logger: logging.Logger
    broadcast_queue: Any = None
    album_queue: Any = None
    admin_feed_queue: Any = None
    task_registry: Any = None
