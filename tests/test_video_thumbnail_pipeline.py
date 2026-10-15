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
            self.token = "bot-token"
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
            assert method.caption is None
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


@pytest.mark.asyncio
async def test_video_album_upload_uses_default_thumbnail_and_no_captions(tmp_path):
    thumbnail = Path(__file__).resolve().parents[1] / "assets" / "thumb.jpg"
    api_root = tmp_path / "bot-api-data"
    source_file = api_root / "bot-token" / "video.mp4"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"fake-mp4")
    settings = SimpleNamespace(
        storage_channel_id="-1001234567890",
        telegram_api_local_mode=True,
        telegram_api_server_files_root=str(api_root),
        telegram_api_host_files_root=str(api_root),
        video_thumbnail_path=str(thumbnail),
        video_ramdisk_path=str(tmp_path / "ramdisk"),
        video_require_tmpfs=False,
        video_max_upload_bytes=2_000_000_000,
        video_ramdisk_reserve_bytes=0,
        telegram_api_timeout_seconds=7200,
    )

    class FakeBot:
        token = "bot-token"

        async def get_file(self, file_id):
            return SimpleNamespace(file_path=str(source_file), file_size=8)

        async def __call__(self, method, request_timeout=None):
            assert request_timeout == 7200
            assert len(method.media) == 2
            assert all(item.caption is None for item in method.media)
            video_item = next(item for item in method.media if hasattr(item, "thumbnail"))
            assert Path(video_item.thumbnail.path).exists()
            assert Path(video_item.media.path).read_bytes() == b"fake-mp4"
            return [
                SimpleNamespace(message_id=100, chat=SimpleNamespace(id=-1001234567890), video=None),
                SimpleNamespace(
                    message_id=101,
                    chat=SimpleNamespace(id=-1001234567890),
                    video=SimpleNamespace(file_id="buffered-video-id"),
                ),
            ]

    pipeline = VideoThumbnailPipeline(SimpleNamespace(bot_id=123, bot=FakeBot(), settings=settings))
    result = await pipeline.store_album_once({
        "media_group_id": "album-1",
        "caption": "caption must not be copied",
        "items": [
            {"message_id": 10, "file_id": "photo-id", "type": "photo"},
            {"message_id": 11, "file_id": "video-id", "type": "video", "file_size": 8},
        ],
    })

    assert result.chat_id == -1001234567890
    assert result.message_ids == [100, 101]
    assert result.video_file_ids == ["buffered-video-id"]
    assert list((tmp_path / "ramdisk").iterdir()) == []
