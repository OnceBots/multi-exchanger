from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram.methods import SendMediaGroup, SendVideo
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo

from app.bot.api_client import map_api_file_path

# All child bots share one 4-GB tmpfs in this single-process platform. A single
# global semaphore prevents concurrent multi-GB staging jobs from exhausting it.
_RAMDISK_SEMAPHORE = asyncio.Semaphore(1)


@dataclass(frozen=True, slots=True)
class StoredVideoMessage:
    chat_id: int | str
    message_id: int
    file_id: str


@dataclass(frozen=True, slots=True)
class StoredAlbumMessages:
    chat_id: int | str
    message_ids: list[int]
    video_file_ids: list[str]


def _jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    """Read JPEG dimensions from its SOF marker using only the standard library."""
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"\xff\xd8":
                return None
            while True:
                byte = stream.read(1)
                if not byte:
                    return None
                if byte != b"\xff":
                    continue
                marker_byte = stream.read(1)
                while marker_byte == b"\xff":
                    marker_byte = stream.read(1)
                if not marker_byte:
                    return None
                marker = marker_byte[0]
                if marker in {0xD9, 0xDA}:
                    return None
                if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
                    continue
                length_bytes = stream.read(2)
                if len(length_bytes) != 2:
                    return None
                segment_length = int.from_bytes(length_bytes, "big")
                if segment_length < 2:
                    return None
                if marker in sof_markers:
                    data = stream.read(min(segment_length - 2, 8))
                    if len(data) < 5:
                        return None
                    height = int.from_bytes(data[1:3], "big")
                    width = int.from_bytes(data[3:5], "big")
                    return width, height
                stream.seek(segment_length - 2, 1)
    except OSError:
        return None


def jpeg_is_valid_thumbnail(path: Path) -> bool:
    """Validate JPEG signature, Telegram size (<200 KB) and max dimensions (320x320)."""
    try:
        if not path.is_file() or path.stat().st_size <= 4 or path.stat().st_size >= 200_000:
            return False
        with path.open("rb") as stream:
            if stream.read(3) != b"\xff\xd8\xff":
                return False
        dimensions = _jpeg_dimensions(path)
        return bool(dimensions and 0 < dimensions[0] <= 320 and 0 < dimensions[1] <= 320)
    except OSError:
        return False


def _chat_id(value: str | int) -> str | int:
    text = str(value).strip()
    return int(text) if text.lstrip("-").isdigit() else text


def _filesystem_type(path: Path) -> str | None:
    """Return the filesystem for a path, selecting the most specific mount."""
    target = path.resolve()
    best_mount: Path | None = None
    best_type: str | None = None
    try:
        with Path("/proc/mounts").open("r", encoding="utf-8") as stream:
            for line in stream:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_raw = parts[1].replace("\\040", " ").replace("\\011", "\t")
                mount = Path(mount_raw)
                try:
                    target.relative_to(mount)
                except ValueError:
                    continue
                if best_mount is None or len(str(mount)) > len(str(best_mount)):
                    best_mount, best_type = mount, parts[2]
    except OSError:
        return None
    return best_type


class VideoThumbnailPipeline:
    """Upload videos once with a fixed thumbnail, then copy buffer messages.

    The caller fans a standalone buffer message out with copy_message, or fans a
    buffered album out with copy_messages. Local files are removed after Telegram
    confirms the upload, before the room fan-out starts.
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.logger = logging.getLogger(f"video-thumbnail.{ctx.bot_id}")

    def is_configured(self) -> bool:
        settings = self.ctx.settings
        if not getattr(settings, "telegram_api_local_mode", False):
            return False
        thumbnail_value = getattr(settings, "video_thumbnail_path", "")
        ramdisk_value = getattr(settings, "video_ramdisk_path", "")
        if not thumbnail_value or not ramdisk_value:
            return False
        if not jpeg_is_valid_thumbnail(Path(thumbnail_value)):
            return False
        if getattr(settings, "video_require_tmpfs", True) and _filesystem_type(Path(ramdisk_value)) != "tmpfs":
            return False
        return True

    def _validate_config(self) -> tuple[Path, Path]:
        settings = self.ctx.settings
        if not settings.storage_channel_id:
            raise RuntimeError("CANAL_STORAGE_ID no está configurado")
        if not settings.telegram_api_local_mode:
            raise RuntimeError("TELEGRAM_API_LOCAL_MODE debe ser true para usar el filesystem local")
        thumbnail = Path(settings.video_thumbnail_path)
        if not jpeg_is_valid_thumbnail(thumbnail):
            raise RuntimeError(f"Thumbnail JPEG ausente/inválido: {thumbnail}; requiere JPEG <200 KB y hasta 320x320")
        ramdisk = Path(settings.video_ramdisk_path)
        if settings.video_require_tmpfs and _filesystem_type(ramdisk) != "tmpfs":
            raise RuntimeError(f"{ramdisk} no está montado como tmpfs; se cancela el buffer para no llenar el disco raíz")
        return thumbnail, ramdisk

    async def _create_tmp_dir(self, ramdisk: Path) -> Path:
        await asyncio.to_thread(ramdisk.mkdir, parents=True, exist_ok=True)
        return Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="video-thumb-", dir=str(ramdisk)))

    async def _resolve_local_video(self, server_file_path: str, file_size_hint: int = 0) -> tuple[Path, int]:
        """Resolve the Bot API absolute file path and inspect size before copying to tmpfs."""
        source_path = map_api_file_path(self.ctx.settings, server_file_path, bot_token=self.ctx.bot.token)
        if not await asyncio.to_thread(source_path.is_file):
            raise RuntimeError(f"El archivo devuelto por el Bot API no está visible para la aplicación: {source_path}")
        actual_size = int(await asyncio.to_thread(lambda: source_path.stat().st_size))
        expected_size = actual_size or int(file_size_hint or 0)
        limit = int(self.ctx.settings.video_max_upload_bytes)
        if expected_size <= 0:
            raise RuntimeError("El vídeo local tiene tamaño desconocido o está vacío")
        if expected_size > limit:
            raise RuntimeError(f"Vídeo de {expected_size} bytes supera VIDEO_MAX_UPLOAD_BYTES={limit}")
        return source_path, actual_size

    async def _stage_local_video(self, source_path: Path, dest: Path, source_size: int) -> int:
        """Copy the already-validated source into tmpfs without blocking the event loop."""
        await asyncio.to_thread(shutil.copyfile, source_path, dest)
        actual_size = int(await asyncio.to_thread(lambda: dest.stat().st_size))
        limit = int(self.ctx.settings.video_max_upload_bytes)
        if actual_size <= 0 or actual_size > limit:
            raise RuntimeError(f"Tamaño local de vídeo no válido: {actual_size}")
        if actual_size != source_size:
            raise RuntimeError(f"Copia local incompleta: origen={source_size} destino={actual_size}")
        return actual_size

    async def store_video_once(self, job: dict[str, Any]) -> StoredVideoMessage:
        settings = self.ctx.settings
        thumbnail, ramdisk = self._validate_config()
        async with _RAMDISK_SEMAPHORE:
            tmp_dir = await self._create_tmp_dir(ramdisk)
            video_path = tmp_dir / "source.mp4"
            try:
                source = await self.ctx.bot.get_file(job["file_id"])
                if not source.file_path:
                    raise RuntimeError("Telegram no devolvió file_path para el vídeo")
                source_path, local_size = await self._resolve_local_video(
                    source.file_path, int(source.file_size or job.get("file_size") or 0),
                )
                byte_size = local_size or int(source.file_size or job.get("file_size") or 0)
                disk = await asyncio.to_thread(shutil.disk_usage, ramdisk)
                required = byte_size + int(settings.video_ramdisk_reserve_bytes)
                if disk.free < required:
                    raise RuntimeError(f"tmpfs sin espacio seguro: libres={disk.free}, requerido={required}")

                actual_size = await self._stage_local_video(source_path, video_path, byte_size)

                staged_thumbnail = tmp_dir / "thumb.jpg"
                await asyncio.to_thread(shutil.copyfile, thumbnail, staged_thumbnail)
                filename = Path(str(job.get("file_name") or "video.mp4")).name or "video.mp4"
                stored = await self.ctx.bot(
                    SendVideo(
                        chat_id=_chat_id(settings.storage_channel_id),
                        video=FSInputFile(video_path, filename=filename),
                        thumbnail=FSInputFile(staged_thumbnail, filename="thumb.jpg"),
                        caption=None,
                        supports_streaming=True,
                    ),
                    request_timeout=int(settings.telegram_api_timeout_seconds),
                )
                if not stored.video or not stored.video.file_id:
                    raise RuntimeError("El canal buffer no devolvió un file_id del vídeo")
                self.logger.info(
                    "video_stored_with_thumbnail bot_id=%s source_message_id=%s buffer_message_id=%s bytes=%s",
                    self.ctx.bot_id, job.get("message_id"), stored.message_id, actual_size,
                )
                return StoredVideoMessage(
                    chat_id=stored.chat.id if stored.chat else _chat_id(settings.storage_channel_id),
                    message_id=int(stored.message_id),
                    file_id=stored.video.file_id,
                )
            finally:
                await asyncio.to_thread(shutil.rmtree, tmp_dir, True)

    async def store_album_once(self, group: dict[str, Any]) -> StoredAlbumMessages:
        """Re-upload a mixed photo/video album once with thumbnails on video items."""
        settings = self.ctx.settings
        thumbnail, ramdisk = self._validate_config()
        items = sorted(group.get("items", []), key=lambda item: int(item.get("message_id", 0)))
        video_items = [item for item in items if item.get("type") == "video"]
        if not video_items:
            raise RuntimeError("El álbum no contiene vídeos para thumbnail")
        if not 2 <= len(items) <= 10:
            raise RuntimeError("Telegram requiere álbumes de entre 2 y 10 elementos")

        async with _RAMDISK_SEMAPHORE:
            tmp_dir = await self._create_tmp_dir(ramdisk)
            try:
                source_files: dict[int, tuple[str, int]] = {}
                total_video_bytes = 0
                limit = int(settings.video_max_upload_bytes)
                for item in video_items:
                    source = await self.ctx.bot.get_file(item["file_id"])
                    if not source.file_path:
                        raise RuntimeError("Telegram no devolvió file_path para un vídeo del álbum")
                    local_path, local_size = await self._resolve_local_video(
                        source.file_path, int(source.file_size or item.get("file_size") or 0),
                    )
                    size = local_size or int(source.file_size or item.get("file_size") or 0)
                    if size > limit:
                        raise RuntimeError(f"Vídeo del álbum de {size} bytes supera VIDEO_MAX_UPLOAD_BYTES={limit}")
                    total_video_bytes += size
                    source_files[int(item["message_id"])] = (local_path, size)
                if total_video_bytes > limit:
                    raise RuntimeError(f"El álbum requiere {total_video_bytes} bytes de vídeo; supera el límite seguro {limit}")
                disk = await asyncio.to_thread(shutil.disk_usage, ramdisk)
                required = total_video_bytes + int(settings.video_ramdisk_reserve_bytes)
                if disk.free < required:
                    raise RuntimeError(f"tmpfs sin espacio seguro para el álbum: libres={disk.free}, requerido={required}")

                upload_media = []
                uploaded_video_ids: list[str] = []
                for item in items:
                    message_id = int(item.get("message_id", 0))
                    if item.get("type") == "photo":
                        upload_media.append(InputMediaPhoto(media=item["file_id"], caption=None))
                        continue
                    if item.get("type") != "video":
                        continue
                    source_path, source_size = source_files[message_id]
                    video_path = tmp_dir / f"video-{message_id}.mp4"
                    actual_size = await self._stage_local_video(source_path, video_path, source_size)
                    staged_thumbnail = tmp_dir / f"thumb-{message_id}.jpg"
                    await asyncio.to_thread(shutil.copyfile, thumbnail, staged_thumbnail)
                    filename = Path(str(item.get("file_name") or f"video-{message_id}.mp4")).name
                    upload_media.append(InputMediaVideo(
                        media=FSInputFile(video_path, filename=filename),
                        thumbnail=FSInputFile(staged_thumbnail, filename="thumb.jpg"),
                        caption=None,
                        supports_streaming=True,
                    ))

                stored_messages = await self.ctx.bot(
                    SendMediaGroup(
                        chat_id=_chat_id(settings.storage_channel_id),
                        media=upload_media,
                    ),
                    request_timeout=int(settings.telegram_api_timeout_seconds),
                )
                stored_ids = [int(message.message_id) for message in stored_messages]
                stored_video_ids = [message.video.file_id for message in stored_messages if getattr(message, "video", None) and message.video.file_id]
                if len(stored_ids) != len(items) or not stored_video_ids:
                    raise RuntimeError("El buffer no devolvió todos los mensajes del álbum")
                buffer_chat_id = stored_messages[0].chat.id if stored_messages[0].chat else _chat_id(settings.storage_channel_id)
                self.logger.info(
                    "video_album_stored_with_thumbnail bot_id=%s source_group=%s buffer_messages=%s video_count=%s bytes=%s",
                    self.ctx.bot_id, group.get("media_group_id"), len(stored_ids), len(stored_video_ids), total_video_bytes,
                )
                return StoredAlbumMessages(buffer_chat_id, stored_ids, stored_video_ids)
            finally:
                await asyncio.to_thread(shutil.rmtree, tmp_dir, True)
