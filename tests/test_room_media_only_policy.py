from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.broadcast_service import BroadcastService


class FakeMediaRepository:
    def __init__(self):
        self.events = []
        self.jobs = []
        self.deliveries = []
        self.completed = []

    async def create_event(self, **kwargs):
        self.events.append(kwargs)
        return {"event_key": kwargs["event_key"]}

    async def create_job(self, *args):
        self.jobs.append(args)

    async def add_delivery(self, *args):
        self.deliveries.append(args)

    async def complete_job(self, *args):
        self.completed.append(args)

    async def jobs_find_one_not_used(self, *args):
        return None


class FakeBot:
    def __init__(self):
        self.calls = []

    async def copy_message(self, **kwargs):
        self.calls.append(("copy_message", kwargs))

    async def copy_messages(self, **kwargs):
        self.calls.append(("copy_messages", kwargs))

    async def send_photo(self, *args, **kwargs):
        self.calls.append(("send_photo", args, kwargs))

    async def send_video(self, *args, **kwargs):
        self.calls.append(("send_video", args, kwargs))

    async def send_document(self, *args, **kwargs):
        self.calls.append(("send_document", args, kwargs))

    async def send_animation(self, *args, **kwargs):
        self.calls.append(("send_animation", args, kwargs))

    async def send_media_group(self, *args, **kwargs):
        self.calls.append(("send_media_group", args, kwargs))


class FakeRoomRepository:
    async def members_for_room(self, bot_id, room_id):
        return [{"user_id": 10}, {"user_id": 11}]


class FakePipeline:
    def __init__(self, *, error=None, stored=None):
        self.error = error
        self.stored = stored
        self.calls = []

    async def store_video_once(self, job):
        self.calls.append(("video", job))
        if self.error:
            raise self.error
        return self.stored

    async def store_album_once(self, group):
        self.calls.append(("album", group))
        if self.error:
            raise self.error
        return self.stored


def context():
    bot = FakeBot()
    media = FakeMediaRepository()
    ctx = SimpleNamespace(
        bot_id=22,
        bot=bot,
        settings=SimpleNamespace(
            broadcast_concurrency_per_bot=1,
            broadcast_max_retries=1,
            video_delivery_delay_seconds=0,
            storage_channel_id="-1001234567890",
            telegram_api_local_mode=True,
            video_thumbnail_path="missing.jpg",
            video_ramdisk_path="/tmp/ramdisk",
            video_require_tmpfs=False,
        ),
        repositories=SimpleNamespace(room=FakeRoomRepository(), media=media),
    )
    return ctx, media


@pytest.mark.asyncio
async def test_room_photo_is_sent_without_caption_even_if_source_has_one():
    ctx, media = context()
    service = BroadcastService(ctx)
    await service._send_with_retry(11, {"media_type": "photo", "file_id": "photo-id", "caption": "private text"})

    assert ctx.bot.calls == [("send_photo", (11, "photo-id"), {"caption": None})]


@pytest.mark.asyncio
async def test_single_video_without_buffer_is_blocked_and_never_sent_directly():
    ctx, _ = context()
    service = BroadcastService(ctx)

    with pytest.raises(RuntimeError, match="miniatura predeterminada"):
        await service._send_with_retry(11, {"media_type": "video", "file_id": "original-video", "caption": "text"})

    assert not any(call[0] == "send_video" for call in ctx.bot.calls)


@pytest.mark.asyncio
async def test_video_album_without_buffer_is_blocked_and_never_sent_directly():
    ctx, _ = context()
    service = BroadcastService(ctx)

    with pytest.raises(RuntimeError, match="miniatura predeterminada"):
        await service._send_album_with_retry(11, {
            "items": [
                {"message_id": 1, "type": "photo", "file_id": "photo"},
                {"message_id": 2, "type": "video", "file_id": "video"},
            ],
            "caption": "text",
        })

    assert not any(call[0] == "send_media_group" for call in ctx.bot.calls)


@pytest.mark.asyncio
async def test_video_pipeline_failure_is_recorded_and_delivery_is_blocked():
    ctx, media = context()
    service = BroadcastService(ctx)
    service.video_thumbnail_enabled = True
    service.video_thumbnail_pipeline = FakePipeline(error=OSError("buffer offline"))

    await service.process_single({
        "room_id": "room-1", "chat_id": 10, "sender_id": 10,
        "message_id": 44, "media_type": "video", "file_id": "source-video",
        "caption": "caption must not leak", "file_size": 10,
    })

    assert not ctx.bot.calls
    assert media.events[0]["caption"] is None
    assert media.jobs[0][-1] == 1
    assert len(media.deliveries) == 1 and media.deliveries[0][3] is False
    assert media.completed[0][-1] == "FAILED"


@pytest.mark.asyncio
async def test_photo_album_never_includes_captions():
    ctx, _ = context()
    service = BroadcastService(ctx)
    await service._send_album_with_retry(11, {
        "items": [
            {"message_id": 1, "type": "photo", "file_id": "photo-1"},
            {"message_id": 2, "type": "photo", "file_id": "photo-2"},
        ],
        "caption": "this must not be sent",
    })

    call = ctx.bot.calls[0]
    assert call[0] == "send_media_group"
    media_group = call[2]["media"]
    assert all(getattr(item, "caption", None) is None for item in media_group)


def test_video_uploaded_as_document_is_classified_as_video():
    from app.services.media_service import MediaService

    service = MediaService(SimpleNamespace(bot_id=22), rooms=object())
    message = SimpleNamespace(
        photo=None,
        video=None,
        document=SimpleNamespace(file_id="video-document-id", mime_type="video/mp4", file_name="clip.mp4"),
        animation=None,
    )

    assert service.media_from_message(message) == ("video", "video-document-id")


def test_regular_document_remains_document():
    from app.services.media_service import MediaService

    service = MediaService(SimpleNamespace(bot_id=22), rooms=object())
    message = SimpleNamespace(
        photo=None,
        video=None,
        document=SimpleNamespace(file_id="pdf-id", mime_type="application/pdf", file_name="guide.pdf"),
        animation=None,
    )

    assert service.media_from_message(message) == ("document", "pdf-id")


@pytest.mark.asyncio
async def test_video_album_pipeline_failure_is_recorded_and_never_sent():
    ctx, media = context()
    service = BroadcastService(ctx)
    service.video_thumbnail_enabled = True
    service.video_thumbnail_pipeline = FakePipeline(error=OSError("buffer offline"))

    await service.process_album({
        "room_id": "room-1",
        "group": {
            "sender_id": 10,
            "media_group_id": "group-55",
            "caption": "caption must not leak",
            "items": [
                {"message_id": 44, "type": "photo", "file_id": "photo-id"},
                {"message_id": 45, "type": "video", "file_id": "video-id", "file_size": 10},
            ],
        },
    })

    assert not ctx.bot.calls
    assert media.events[0]["caption"] is None
    assert media.deliveries and all(item[3] is False for item in media.deliveries)
    assert media.completed[0][-1] == "FAILED"
