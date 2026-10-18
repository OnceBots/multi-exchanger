from __future__ import annotations

from pathlib import Path
import re


def _bot_directories_from_token(token: str | None) -> tuple[str, ...]:
    """Return accepted on-disk directory names for a bot, without logging them.

    Bot API builds/deployments can store a bot's files under either the full
    token directory or the numeric bot-ID directory. The full token stays only
    in memory and is never included in errors or logs.
    """
    if token is None:
        return ()
    value = token.strip()
    if not value or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError("Token inválido para resolver un archivo local del Bot API.")
    bot_id = value.split(":", 1)[0]
    if not bot_id.isdigit():
        raise ValueError("No se pudo identificar el directorio del bot.")
    # Prefer the layout actually observed on the running server (full token),
    # while retaining compatibility with installations using only numeric IDs.
    return tuple(dict.fromkeys((value, bot_id)))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _bot_directory_names(host_root: Path, bot_id: str, bot_dirs: tuple[str, ...]) -> tuple[str, ...]:
    """List on-disk directories owned by this bot ID, including rotated tokens.

    Local Bot API data can retain a directory named ``<id>:<old-token>`` after
    the bot token has been rotated. We only accept directories whose prefix is
    this bot's numeric ID; directories belonging to other bots are never used.
    """
    names = list(bot_dirs)
    try:
        for child in host_root.iterdir():
            if child.is_dir() and (child.name == bot_id or child.name.startswith(f"{bot_id}:")):
                if child.name not in names:
                    names.append(child.name)
    except OSError:
        # Candidate checks below will produce a safe, generic diagnostic.
        pass
    return tuple(names)


def _is_our_bot_directory(name: str, *, bot_id: str, bot_dirs: tuple[str, ...]) -> bool:
    return name in bot_dirs or name == bot_id or name.startswith(f"{bot_id}:")


def map_api_file_path(
    settings,
    server_file_path: str | Path,
    *,
    bot_token: str | None = None,
) -> Path:
    """Map a local Bot API file path into the app's read-only shared volume.

    Handles container-absolute paths, host-volume paths, relative paths, and
    Bot API directories retained after token rotation. Resolution is strictly
    scoped to folders belonging to the numeric bot ID and rejects traversal or
    ambiguous relative matches so one bot cannot receive another bot's media.
    """
    if not settings.telegram_api_local_mode:
        raise RuntimeError("El mapeo local requiere TELEGRAM_API_LOCAL_MODE=true.")

    bot_dirs = _bot_directories_from_token(bot_token)
    if not bot_dirs:
        raise ValueError("No se puede resolver una ruta local sin el token del bot.")
    bot_id = bot_dirs[-1] if bot_dirs[-1].isdigit() else bot_dirs[0].split(":", 1)[0]
    server_root = Path(settings.telegram_api_server_files_root).expanduser().resolve()
    host_root = Path(settings.telegram_api_host_files_root).expanduser().resolve()
    raw_text = str(server_file_path).strip()
    if not raw_text or raw_text == "." or "\x00" in raw_text:
        raise ValueError("Telegram no devolvió una ruta de archivo válida.")

    raw = Path(raw_text)
    if not raw.is_absolute() and any(part in {"..", "."} for part in raw.parts):
        raise ValueError("La ruta relativa devuelta por Telegram no es segura.")

    directory_names = _bot_directory_names(host_root, bot_id, bot_dirs)
    explicit_candidates: list[Path] = []
    fallback_candidates: list[Path] = []

    def is_our_dir(name: str) -> bool:
        return _is_our_bot_directory(name, bot_id=bot_id, bot_dirs=directory_names)

    if raw.is_absolute():
        absolute = raw.resolve(strict=False)

        # The API wrapper may already have translated the path to the host mount.
        if _is_within(absolute, host_root):
            rel = absolute.relative_to(host_root)
            if rel.parts and is_our_dir(rel.parts[0]):
                explicit_candidates.append(absolute)

        # Normal local Bot API response: /var/lib/telegram-bot-api/<id>:<token>/...
        if _is_within(absolute, server_root):
            rel = absolute.relative_to(server_root)
            if rel.parts and is_our_dir(rel.parts[0]):
                explicit_candidates.append(host_root / rel)
            elif rel.parts:
                explicit_bot = re.match(r"^(\d+):", rel.parts[0])
                if explicit_bot and explicit_bot.group(1) != bot_id:
                    raise FileNotFoundError("La ruta del Bot API pertenece a otro bot; se rechazó para evitar mezclar archivos.")
                # Some builds return an absolute path whose suffix is relative
                # to the per-bot directory, omitting its directory name.
                fallback_candidates.extend(host_root / name / rel for name in directory_names)
        else:
            # Accommodate a server returning its own absolute data path even if
            # that path differs from SERVER_FILES_ROOT, but only when the path
            # explicitly identifies this bot's own directory.
            parts = absolute.parts
            for index, part in enumerate(parts):
                if is_our_dir(part) and index < len(parts) - 1:
                    explicit_candidates.append(host_root.joinpath(*parts[index:]))
                    break
    else:
        if raw.parts and is_our_dir(raw.parts[0]):
            explicit_candidates.append(host_root / raw)
        else:
            fallback_candidates.extend(host_root / name / raw for name in directory_names)

    def existing_safe_file(candidate: Path) -> Path | None:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not _is_within(resolved, host_root) or not resolved.is_file():
            return None
        try:
            relative = resolved.relative_to(host_root)
        except ValueError:
            return None
        if not relative.parts or not is_our_dir(relative.parts[0]):
            return None
        return resolved

    # When the API response names the exact directory, it is authoritative.
    seen: set[str] = set()
    for candidate in explicit_candidates:
        result = existing_safe_file(candidate)
        if result is not None and str(result) not in seen:
            return result
        if result is not None:
            seen.add(str(result))

    # A path without the directory name can be resolved only if it identifies
    # exactly one file for this bot. Never guess between stale/duplicate files.
    matches: dict[str, Path] = {}
    for candidate in fallback_candidates:
        result = existing_safe_file(candidate)
        if result is not None:
            matches[str(result)] = result
    if len(matches) == 1:
        return next(iter(matches.values()))
    if len(matches) > 1:
        raise FileNotFoundError(
            "La ruta local del vídeo coincide con varios archivos del mismo bot; "
            "se bloqueó la entrega para evitar enviar un archivo equivocado."
        )

    raise FileNotFoundError(
        "No se pudo localizar o leer el archivo de Telegram en el volumen compartido. "
        "Verificá el directorio de datos y los permisos de lectura del contenedor app."
    )
