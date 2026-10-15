from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd().resolve()

API_CLIENT = r'''from __future__ import annotations

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
'''

THUMBNAIL_HANDLER = '''    @router.message(F.photo)
    async def thumbnail_photo(message: Message) -> None:
        if not message.from_user:
            return
        uid = int(message.from_user.id)
        session = await manager.repositories.session.get(0, uid)
        if not session or session.get("step") != "set_video_thumbnail":
            return
        if not is_super_admin(uid):
            await manager.repositories.session.clear(0, uid)
            await message.answer("⛔ Solo el superadministrador puede cambiar la miniatura.")
            return
        try:
            photo = message.photo[-1]
            max_image_bytes = 10 * 1024 * 1024

            if manager.settings.telegram_api_local_mode:
                # In local Bot API mode use the shared filesystem; never build an
                # /file/bot... URL, because getFile may return a local disk path.
                file_info = await manager.master_bot.get_file(photo.file_id)
                if not file_info.file_path:
                    raise ValueError("Telegram no devolvió una ruta de archivo válida.")
                local_path = map_api_file_path(
                    manager.settings,
                    file_info.file_path,
                    bot_token=manager.master_bot.token,
                )
                file_size = await asyncio.to_thread(lambda: local_path.stat().st_size)
                if file_size <= 0 or file_size > max_image_bytes:
                    raise ValueError("La imagen está vacía o supera el límite de 10 MB.")

                def read_image_limited() -> bytes:
                    with local_path.open("rb") as stream:
                        return stream.read(max_image_bytes + 1)

                image_bytes = await asyncio.to_thread(read_image_limited)
                if not image_bytes or len(image_bytes) > max_image_bytes:
                    raise ValueError("La imagen está vacía o supera el límite de 10 MB.")
                if len(image_bytes) != file_size:
                    raise OSError("La lectura de la imagen quedó incompleta.")
            else:
                image_buffer = BytesIO()
                await manager.master_bot.download(
                    photo.file_id,
                    destination=image_buffer,
                )
                image_bytes = image_buffer.getvalue()
                if not image_bytes or len(image_bytes) > max_image_bytes:
                    raise ValueError("La imagen está vacía o supera el límite de 10 MB.")

            saved_path = await asyncio.to_thread(
                save_default_video_thumbnail,
                image_bytes,
                manager.settings.video_thumbnail_path,
            )
            await manager.repositories.session.clear(0, uid)
            await message.answer(
                "✅ <b>Miniatura actualizada</b>\\n\\n"
                "Los próximos videos procesados por los bots hijos usarán esta imagen.\\n"
                f"📁 Ruta configurada: <code>{html.escape(str(saved_path))}</code>\\n\\n"
                "Los videos ya subidos a Telegram no se modifican retroactivamente."
            )
        except Exception as exc:
            # Do not expose bot tokens or absolute filesystem paths in Telegram.
            detail = str(exc).replace(manager.master_bot.token, "<token>")
            detail = detail.replace(
                str(manager.settings.telegram_api_host_files_root),
                "<volumen-local>",
            )
            await message.answer(
                "❌ No pude guardar esa imagen. Probá enviarla como foto JPG/PNG.\\n"
                f"Detalle: <code>{html.escape(detail[:250])}</code>"
            )

'''


def safe_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=str(path.parent)) as f:
        f.write(content)
        temp_name = f.name
    os.replace(temp_name, path)


def update_config(path: Path, text: str) -> str:
    if "telegram_api_server_files_root" not in text or "telegram_api_host_files_root" not in text:
        raise RuntimeError("app/config.py no define las dos raíces del Bot API; no se puede aplicar el parche automáticamente.")
    server_pattern = r'(telegram_api_server_files_root\s*=\s*_path\("TELEGRAM_API_SERVER_FILES_ROOT",\s*")[^"]*("\))'
    host_pattern = r'(telegram_api_host_files_root\s*=\s*_path\("TELEGRAM_API_HOST_FILES_ROOT",\s*")[^"]*("\))'
    if not re.search(server_pattern, text):
        raise RuntimeError("No se encontró una asignación reconocible para TELEGRAM_API_SERVER_FILES_ROOT.")
    if not re.search(host_pattern, text):
        raise RuntimeError("No se encontró una asignación reconocible para TELEGRAM_API_HOST_FILES_ROOT.")
    updated = re.sub(server_pattern, r'\1/var/lib/telegram-bot-api\2', text, count=1)
    updated = re.sub(host_pattern, r'\1/root/tg-bot-api/data\2', updated, count=1)
    # The old failed config download once contained a bare class-body name,
    # which raises NameError at import time. Correct it if present.
    updated = re.sub(r'(?m)^(\s*)token_encryption_key\s*$', r'\1token_encryption_key: str', updated)
    return updated


def update_handler(text: str) -> str:
    pattern = re.compile(
        r'^    @router\.message\(F\.photo\)\n'
        r'    async def thumbnail_photo\(message: Message\) -> None:.*?'
        r'(?=^    @router\.(?:message|callback_query)\()',
        re.M | re.S,
    )
    updated, count = pattern.subn(lambda _match: THUMBNAIL_HANDLER, text)
    if count != 1:
        raise RuntimeError(
            f"Se esperaba encontrar una sola función thumbnail_photo con el siguiente decorador; se encontraron {count}. No se modificó el archivo."
        )
    for line, anchor in [
        ("import asyncio", "import html"),
        ("from io import BytesIO", "import html"),
        ("from app.bot.api_client import map_api_file_path", "from aiogram.filters import Command, CommandStart"),
        ("from app.services.thumbnail_settings import save_default_video_thumbnail", "from app.services.bot_lifecycle import botfather_instructions, schedule_label"),
    ]:
        if line not in updated:
            if anchor not in updated:
                raise RuntimeError(f"No se pudo insertar el import requerido: {line}")
            updated = updated.replace(anchor, anchor + "\n" + line, 1)
    return updated


def update_pipeline(text: str) -> str:
    if "map_api_file_path" not in text:
        raise RuntimeError("video_thumbnail_pipeline.py no usa map_api_file_path; hace falta revisar esa versión manualmente.")
    updated = re.sub(
        r'map_api_file_path\(self\.ctx\.settings,\s*server_file_path\s*\)',
        'map_api_file_path(self.ctx.settings, server_file_path, bot_token=self.ctx.bot.token)',
        text,
    )
    if "map_api_file_path(self.ctx.settings, server_file_path, bot_token=self.ctx.bot.token)" not in updated:
        raise RuntimeError("No se pudo asegurar que el pipeline use el token del bot al resolver rutas.")
    return updated


def update_env_example(path: Path, text: str) -> str:
    lines = text.splitlines()
    values = {
        "TELEGRAM_API_SERVER_FILES_ROOT": "/var/lib/telegram-bot-api",
        "TELEGRAM_API_HOST_FILES_ROOT": "/root/tg-bot-api/data",
    }
    for key, value in values.items():
        prefix = key + "="
        indexes = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        if indexes:
            first = indexes[0]
            lines[first] = prefix + value
            for i in reversed(indexes[1:]):
                del lines[i]
        else:
            lines.append(prefix + value)
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Corrige el mapeo de archivos del Telegram Bot API local.")
    parser.add_argument("--check", action="store_true", help="Verifica que el parche se pueda aplicar, sin escribir archivos.")
    args = parser.parse_args()

    required = [
        ROOT / "app/config.py",
        ROOT / "app/bot/master_handlers.py",
        ROOT / "app/services/video_thumbnail_pipeline.py",
        ROOT / "app/bot/api_client.py",
    ]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    if missing:
        print("ERROR: ejecutá este script desde la raíz del repositorio. Faltan: " + ", ".join(missing), file=sys.stderr)
        return 2

    paths = {
        ROOT / "app/config.py": update_config(ROOT / "app/config.py", (ROOT / "app/config.py").read_text(encoding="utf-8")),
        ROOT / "app/bot/master_handlers.py": update_handler((ROOT / "app/bot/master_handlers.py").read_text(encoding="utf-8")),
        ROOT / "app/services/video_thumbnail_pipeline.py": update_pipeline((ROOT / "app/services/video_thumbnail_pipeline.py").read_text(encoding="utf-8")),
        ROOT / "app/bot/api_client.py": API_CLIENT,
    }
    env_example = ROOT / ".env.example"
    if env_example.is_file():
        paths[env_example] = update_env_example(env_example, env_example.read_text(encoding="utf-8"))

    if args.check:
        print("OK: el parche puede aplicarse sin sobrescribir el archivo .env ni la clave de cifrado.")
        print("Archivos que cambiarían:")
        for path in paths:
            print(" -", path.relative_to(ROOT))
        return 0

    backup_root = ROOT.parent / f"{ROOT.name}_backup_local_api_paths_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_root.mkdir(parents=True, exist_ok=False)
    for path in paths:
        relative = path.relative_to(ROOT)
        backup = backup_root / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, backup)
    for path, content in paths.items():
        safe_write(path, content)

    print("OK: parche aplicado.")
    print(f"Respaldo de los archivos anteriores: {backup_root}")
    print("Siguiente: python -m compileall -q app tests")
    print("Después: python -m pytest -q tests/test_local_api_file_path_mapping.py tests/test_vultr_api_client.py")
    # Warn only; do not modify .env (it contains deployment-specific settings).
    env_path = ROOT / ".env"
    if env_path.exists():
        configured = {}
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("TELEGRAM_API_SERVER_FILES_ROOT="):
                configured["server"] = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_API_HOST_FILES_ROOT="):
                configured["host"] = line.split("=", 1)[1].strip()
        if configured.get("server") not in (None, "", "/var/lib/telegram-bot-api") or configured.get("host") not in (None, "", "/root/tg-bot-api/data"):
            print("ADVERTENCIA: tu .env contiene raíces explícitas distintas. Revisá TELEGRAM_API_SERVER_FILES_ROOT y TELEGRAM_API_HOST_FILES_ROOT; no se modificó .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
