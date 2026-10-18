from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram.methods import SendMediaGroup, SendVideo
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo

from app.bot.api_client import map_api_file_path


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


class VideoThumbnailPipeline:
    """Apply the brand image as the visible video cover, then copy the buffer message.

    The source video is loaded from the Local Bot API shared volume. Both the legacy
    ``thumbnail`` metadata and the visible ``cover`` are uploaded as multipart files.
    Telegram may generate/ignore ``thumbnail`` for videos; ``cover`` is the explicit
    cover parameter that controls the image displayed on the video message. Room and
    admin delivery copy only the buffer message created here.
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.logger = logging.getLogger(f"video-thumbnail.{ctx.bot_id}")

    def configuration_issues(self) -> list[str]:
        settings = self.ctx.settings
        issues: list[str] = []
        if not getattr(settings, "telegram_api_local_mode", False):
            issues.append("TELEGRAM_API_LOCAL_MODE_no_es_true")
        if not str(getattr(settings, "storage_channel_id", "") or "").strip():
            issues.append("CANAL_STORAGE_ID_no_configurado")
        if not str(getattr(settings, "telegram_api_server_files_root", "") or "").strip():
            issues.append("TELEGRAM_API_SERVER_FILES_ROOT_vacio")
        if not str(getattr(settings, "telegram_api_host_files_root", "") or "").strip():
            issues.append("TELEGRAM_API_HOST_FILES_ROOT_vacio")
        thumbnail_value = str(getattr(settings, "video_thumbnail_path", "") or "").strip()
        if not thumbnail_value:
            issues.append("VIDEO_THUMBNAIL_PATH_vacio")
        elif not jpeg_is_valid_thumbnail(Path(thumbnail_value)):
            issues.append("miniatura_no_es_JPEG_valido_de_hasta_320x320_y_200KB")
        return issues

    def is_configured(self) -> bool:
        return not self.configuration_issues()

    def _validate_config(self) -> Path:
        issues = self.configuration_issues()
        if issues:
            raise RuntimeError("Configuración de miniaturas incompleta: " + ", ".join(issues))
        return Path(self.ctx.settings.video_thumbnail_path).expanduser().resolve()

    async def _resolve_local_video(self, server_file_path: str, file_size_hint: int = 0) -> tuple[Path, int]:
        """Map getFile's server path to a verified host-mounted source file."""
        source_path = map_api_file_path(
            self.ctx.settings, server_file_path, bot_token=self.ctx.bot.token
        )
        if not await asyncio.to_thread(source_path.is_file):
            raise RuntimeError("El archivo devuelto por el Bot API no está visible en el volumen compartido")
        actual_size = int(await asyncio.to_thread(lambda: source_path.stat().st_size))
        if actual_size <= 0:
            raise RuntimeError("El vídeo local está vacío o su tamaño es desconocido")
        limit = int(self.ctx.settings.video_max_upload_bytes)
        if actual_size > limit:
            raise RuntimeError(f"Vídeo de {actual_size} bytes supera VIDEO_MAX_UPLOAD_BYTES={limit}")
        return source_path, actual_size

    def _video_input(self, host_path: Path, filename: str) -> FSInputFile:
        # Force multipart for the video itself; the caller attaches both thumbnail
        # and cover as multipart files. A custom visible cover requires Bot API support
        # for the `cover` parameter (Bot API 8.3+).
        return FSInputFile(host_path, filename=filename)

    async def store_video_once(self, job: dict[str, Any]) -> StoredVideoMessage:
        settings = self.ctx.settings
        thumbnail = self._validate_config()
        try:
            source = await self.ctx.bot.get_file(job["file_id"])
        except Exception as exc:
            if "file is too big" in str(exc).lower():
                raise RuntimeError(
                    "getFile rechazó el vídeo como demasiado grande. El bot debe usar el "
                    "Telegram Bot API local iniciado con --local; TELEGRAM_API_BASE_URL "
                    "debe apuntar a ese servidor (no a api.telegram.org ni a una instancia "
                    "del Bot API sin --local)."
                ) from exc
            raise
        if not source.file_path:
            raise RuntimeError("Telegram no devolvió file_path para el vídeo")

        source_path, byte_size = await self._resolve_local_video(
            source.file_path, int(source.file_size or job.get("file_size") or 0)
        )
        filename = Path(str(job.get("file_name") or "video.mp4")).name or "video.mp4"
        video_input = self._video_input(source_path, filename)
        upload_mode = "multipart_video_thumbnail_and_cover"
        stored = await self.ctx.bot(
            SendVideo(
                chat_id=_chat_id(settings.storage_channel_id),
                video=video_input,
                # `thumbnail` may be ignored in favor of a server-generated frame.
                # `cover` sets the visible message cover. Both are uploaded with the
                # video via multipart; the cover uses an API parameter available in
                # Telegram Bot API 8.3 and newer.
                thumbnail=FSInputFile(thumbnail, filename="thumb.jpg"),
                cover=FSInputFile(thumbnail, filename="cover.jpg"),
                caption=None,
                supports_streaming=True,
            ),
            request_timeout=int(settings.telegram_api_timeout_seconds),
        )
        if not stored.video or not stored.video.file_id:
            raise RuntimeError("El canal buffer no devolvió un file_id del vídeo")
        self.logger.info(
            "video_stored_with_thumbnail_and_cover bot_id=%s source_message_id=%s buffer_message_id=%s bytes=%s upload_mode=%s storage_channel_id=%s",
            self.ctx.bot_id, job.get("message_id"), stored.message_id, byte_size, upload_mode, settings.storage_channel_id,
        )
        return StoredVideoMessage(
            chat_id=stored.chat.id if stored.chat else _chat_id(settings.storage_channel_id),
            message_id=int(stored.message_id),
            file_id=stored.video.file_id,
        )

    async def store_album_once(self, group: dict[str, Any]) -> StoredAlbumMessages:
        """Upload a mixed photo/video album once, with the default thumb per video."""
        settings = self.ctx.settings
        thumbnail = self._validate_config()
        items = sorted(group.get("items", []), key=lambda item: int(item.get("message_id", 0)))
        video_items = [item for item in items if item.get("type") == "video"]
        if not video_items:
            raise RuntimeError("El álbum no contiene vídeos para miniatura")
        if not 2 <= len(items) <= 10:
            raise RuntimeError("Telegram requiere álbumes de entre 2 y 10 elementos")

        source_files: dict[int, tuple[Path, int]] = {}
        total_video_bytes = 0
        limit = int(settings.video_max_upload_bytes)
        for item in video_items:
            try:
                source = await self.ctx.bot.get_file(item["file_id"])
            except Exception as exc:
                if "file is too big" in str(exc).lower():
                    raise RuntimeError(
                        "getFile rechazó un vídeo del álbum como demasiado grande. "
                        "El Telegram Bot API local debe ejecutarse con --local y la app "
                        "debe usar su URL interna."
                    ) from exc
                raise
            if not source.file_path:
                raise RuntimeError("Telegram no devolvió file_path para un vídeo del álbum")
            local_path, size = await self._resolve_local_video(
                source.file_path, int(source.file_size or item.get("file_size") or 0)
            )
            total_video_bytes += size
            if total_video_bytes > limit:
                raise RuntimeError(
                    f"El álbum requiere {total_video_bytes} bytes de vídeo; supera el límite {limit}"
                )
            source_files[int(item["message_id"])] = (local_path, size)

        upload_media = []
        for item in items:
            message_id = int(item.get("message_id", 0))
            if item.get("type") == "photo":
                upload_media.append(InputMediaPhoto(media=item["file_id"], caption=None))
                continue
            if item.get("type") != "video":
                raise RuntimeError(f"Tipo no permitido dentro del álbum: {item.get('type')}")

            source_path, _source_size = source_files[message_id]
            filename = Path(str(item.get("file_name") or f"video-{message_id}.mp4")).name
            video_input = self._video_input(source_path, filename)
            upload_media.append(InputMediaVideo(
                media=video_input,
                thumbnail=FSInputFile(thumbnail, filename=f"thumb-{message_id}.jpg"),
                cover=FSInputFile(thumbnail, filename=f"cover-{message_id}.jpg"),
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
        stored_video_ids = [
            message.video.file_id
            for message in stored_messages
            if getattr(message, "video", None) and message.video.file_id
        ]
        if len(stored_ids) != len(items) or len(stored_video_ids) != len(video_items):
            raise RuntimeError("El buffer no devolvió todos los mensajes/vídeos del álbum")
        buffer_chat_id = (
            stored_messages[0].chat.id
            if stored_messages[0].chat else _chat_id(settings.storage_channel_id)
        )
        self.logger.info(
            "video_album_stored_with_thumbnail_and_cover bot_id=%s source_group=%s buffer_messages=%s video_count=%s bytes=%s upload_mode=multipart_video_thumbnail_and_cover",
            self.ctx.bot_id, group.get("media_group_id"), len(stored_ids), len(stored_video_ids), total_video_bytes,
        )
        return StoredAlbumMessages(buffer_chat_id, stored_ids, stored_video_ids)
