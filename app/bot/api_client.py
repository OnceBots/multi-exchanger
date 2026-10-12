from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import SimpleFilesPathWrapper, TelegramAPIServer


def build_api_server(settings) -> TelegramAPIServer:
    """Construct the configured Bot API endpoint and local file-path mapping.

    In local mode, getFile returns a server filesystem path. SimpleFilesPathWrapper
    maps that path to the host-visible directory mounted by the Bot API container.
    """
    base = settings.telegram_api_base_url.rstrip("/")
    if not settings.telegram_api_local_mode:
        return TelegramAPIServer.from_base(base)

    wrapper = SimpleFilesPathWrapper(
        server_path=Path(settings.telegram_api_server_files_root),
        local_path=Path(settings.telegram_api_host_files_root),
    )
    return TelegramAPIServer.from_base(base, is_local=True, wrap_local_file=wrapper)


def build_api_session(settings) -> AiohttpSession:
    return AiohttpSession(api=build_api_server(settings), timeout=60)


def build_bot(token: str, settings, *, default: DefaultBotProperties | None = None) -> Bot:
    """Create every bot (master, child, and validation bot) against the same API server."""
    return Bot(
        token=token.strip(),
        session=build_api_session(settings),
        default=default or DefaultBotProperties(),
    )


def map_api_file_path(settings, server_file_path: str | Path) -> Path:
    """Map a path returned by local Bot API getFile to the path visible by this app.

    This allows the thumbnail pipeline to copy files directly from the shared
    Bot API data volume using asyncio.to_thread, instead of downloading over HTTP.
    """
    if not settings.telegram_api_local_mode:
        raise RuntimeError("El mapeo de archivos requiere TELEGRAM_API_LOCAL_MODE=true")
    wrapper = SimpleFilesPathWrapper(
        server_path=Path(settings.telegram_api_server_files_root),
        local_path=Path(settings.telegram_api_host_files_root),
    )
    return Path(wrapper.to_local(Path(server_file_path)))
