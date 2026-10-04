from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, Dispatcher


@dataclass(slots=True)
class BotContext:
    bot_id: int
    bot: Bot
    dispatcher: Dispatcher
    db: Any
    settings: Any
    runtime: Any
    logger: logging.Logger
    services: Any
    repositories: Any
    config: Any
