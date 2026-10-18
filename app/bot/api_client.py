from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import SimpleFilesPathWrapper, TelegramAPIServer

from app.services.local_file_paths import map_api_file_path


class BotScopedFilesPathWrapper(SimpleFilesPathWrapper):
    """Aiogram wrapper supporting absolute and per-bot-relative local paths."""

    def __init__(self, *, server_path: Path, local_path: Path, settings, bot_token: str | None):
        super().__init__(server_path=server_path, local_path=local_path)
        self._settings = settings
        self._bot_token = bot_token

    def to_local(self, path: Path | str) -> Path | str:
        return map_api_file_path(self._settings, path, bot_token=self._bot_token)


def build_api_server(settings, token: str | None = None) -> TelegramAPIServer:
    """Build the configured Bot API endpoint and local filesystem mapping."""
    base = settings.telegram_api_base_url.rstrip("/")
    if not settings.telegram_api_local_mode:
        return TelegramAPIServer.from_base(base)

    wrapper = BotScopedFilesPathWrapper(
        server_path=Path(settings.telegram_api_server_files_root).expanduser().resolve(),
        local_path=Path(settings.telegram_api_host_files_root).expanduser().resolve(),
        settings=settings,
        bot_token=token,
    )
    return TelegramAPIServer.from_base(base, is_local=True, wrap_local_file=wrapper)


def build_api_session(settings, token: str | None = None) -> AiohttpSession:
    # Honor the configured long timeout; large local-API uploads can take more
    # than the library's usual 60-second default.
    timeout = max(60, int(getattr(settings, "telegram_api_timeout_seconds", 7200)))
    return AiohttpSession(api=build_api_server(settings, token=token), timeout=timeout)


def build_bot(
    token: str,
    settings,
    *,
    default: DefaultBotProperties | None = None,
) -> Bot:
    """Create Master/child/validation bots using the same API configuration."""
    clean_token = token.strip()
    return Bot(
        token=clean_token,
        session=build_api_session(settings, token=clean_token),
        default=default or DefaultBotProperties(),
    )
