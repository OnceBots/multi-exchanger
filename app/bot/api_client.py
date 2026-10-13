from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import SimpleFilesPathWrapper, TelegramAPIServer


def _safe_token_dir(token: str | None) -> str | None:
    """Validate a bot token for use as a filesystem directory component."""
    if token is None:
        return None
    value = token.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("Token inválido para resolver un archivo local del Bot API.")
    return value


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def map_api_file_path(
    settings,
    server_file_path: str | Path,
    *,
    bot_token: str | None = None,
) -> Path:
    """Map local Bot API getFile paths to the shared host volume safely.

    In --local mode the server may return an absolute container path or a
    relative per-bot path such as ``photos/file_1.jpg``. The target must exist
    under the configured host root and, when provided, that bot's own folder.
    Error text never exposes the token or untrusted path.
    """
    if not settings.telegram_api_local_mode:
        raise RuntimeError("El mapeo local requiere TELEGRAM_API_LOCAL_MODE=true.")

    token_dir = _safe_token_dir(bot_token)
    server_root = Path(settings.telegram_api_server_files_root).expanduser().resolve()
    host_root = Path(settings.telegram_api_host_files_root).expanduser().resolve()
    raw_text = str(server_file_path).strip()
    if not raw_text or raw_text == "." or "\x00" in raw_text:
        raise ValueError("Telegram no devolvió una ruta de archivo válida.")

    raw = Path(raw_text)
    candidates: list[Path] = []

    if raw.is_absolute():
        resolved_raw = raw.resolve(strict=False)
        # Already mapped to the host: accept only the shared root and this bot.
        if _is_within(resolved_raw, host_root):
            relative_host = resolved_raw.relative_to(host_root)
            if token_dir is None or (relative_host.parts and relative_host.parts[0] == token_dir):
                candidates.append(resolved_raw)

        # Standard local Bot API path: <container-root>/<token>/photos/file.jpg.
        if _is_within(resolved_raw, server_root):
            relative_server = resolved_raw.relative_to(server_root)
            if token_dir is not None:
                if relative_server.parts and relative_server.parts[0] == token_dir:
                    candidates.append(host_root / relative_server)
                elif relative_server.parts:
                    # Defensive support for a server path missing its bot prefix.
                    candidates.append(host_root / token_dir / relative_server)
            elif relative_server.parts and relative_server.parts[0] not in {".", ".."}:
                # Absolute server paths are unambiguous only if namespaced by bot.
                candidates.append(host_root / relative_server)
    else:
        if any(part == ".." for part in raw.parts):
            raise ValueError("La ruta relativa devuelta por Telegram no es segura.")
        if token_dir is None:
            raise ValueError("No se puede resolver una ruta relativa sin el token del bot.")
        if raw.parts and raw.parts[0] == token_dir:
            candidates.append(host_root / raw)
        else:
            candidates.append(host_root / token_dir / raw)

    token_root = (host_root / token_dir).resolve(strict=False) if token_dir else None
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if not _is_within(resolved, host_root) or not resolved.is_file():
            continue
        if token_root is not None and not _is_within(resolved, token_root):
            continue
        return resolved

    raise FileNotFoundError(
        "El archivo de Telegram no existe en el volumen compartido del bot. "
        "Verificá las raíces del Bot API y el montaje Docker."
    )


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
