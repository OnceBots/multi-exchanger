from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.video_thumbnail_pipeline import VideoThumbnailPipeline, jpeg_is_valid_thumbnail


def test_default_thumbnail_is_telegram_compatible():
    thumbnail = Path(__file__).resolve().parents[1] / "assets" / "thumb.jpg"
    assert jpeg_is_valid_thumbnail(thumbnail)
    assert thumbnail.stat().st_size < 200_000


@pytest.mark.asyncio
async def test_video_is_uploaded_once_and_temp_file_removed(tmp_path):
    thumbnail = Path(__file__).resolve().parents[1] / "assets" / "thumb.jpg"
    thumbnail_server_root = tmp_path / "bot-api-data"
    source_file = thumbnail_server_root / "bot-token" / "source.mp4"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"fake-mp4")
    settings = SimpleNamespace(
        storage_channel_id="-1001234567890",
        telegram_api_local_mode=True,
        telegram_api_server_files_root=str(thumbnail_server_root),
        telegram_api_host_files_root=str(thumbnail_server_root),
        video_thumbnail_path=str(thumbnail),
        video_ramdisk_path=str(tmp_path / "ramdisk"),
        video_require_tmpfs=False,
        video_max_upload_bytes=2_000_000_000,
        video_ramdisk_reserve_bytes=0,
        telegram_api_timeout_seconds=7200,
    )

    class FakeBot:
        def __init__(self):
            self.calls = []

        async def get_file(self, file_id):
            return SimpleNamespace(file_path=str(thumbnail_server_root / "bot-token" / "source.mp4"), file_size=8)

        async def download_file(self, file_path, destination, timeout):
            raise AssertionError("No se debe descargar sobre HTTP: se copia desde el volumen local")

        async def __call__(self, method, request_timeout=None):
            # Prove upload uses new multipart media, thumbnail, and an extended timeout.
            assert request_timeout == 7200
            assert Path(method.video.path).exists()
            assert Path(method.thumbnail.path).exists()
            assert Path(method.video.path).read_bytes() == b"fake-mp4"
            self.calls.append(("send_video", method.chat_id))
            return SimpleNamespace(
                chat=SimpleNamespace(id=-1001234567890),
                message_id=77,
                video=SimpleNamespace(file_id="new-file-id"),
            )

    bot = FakeBot()
    ctx = SimpleNamespace(bot_id=123, bot=bot, settings=settings)
    pipeline = VideoThumbnailPipeline(ctx)
    result = await pipeline.store_video_once({"file_id": "old-file-id", "message_id": 21, "caption": "caption"})

    assert result.chat_id == -1001234567890
    assert result.message_id == 77
    assert result.file_id == "new-file-id"
    assert [call[0] for call in bot.calls] == ["send_video"]
    assert list((tmp_path / "ramdisk").iterdir()) == []


def test_thumbnail_requires_jpeg_and_size(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not jpeg")
    assert not jpeg_is_valid_thumbnail(bad)
