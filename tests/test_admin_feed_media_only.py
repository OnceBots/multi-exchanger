from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.admin_feed_service import AdminFeedService


class FakeAdminCollection:
    async def find_one(self, query):
        return None


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_photo(self, *args, **kwargs):
        self.calls.append(("photo", args, kwargs))

    async def send_video(self, *args, **kwargs):
        self.calls.append(("video", args, kwargs))

    async def send_animation(self, *args, **kwargs):
        self.calls.append(("animation", args, kwargs))

    async def send_document(self, *args, **kwargs):
        self.calls.append(("document", args, kwargs))

    async def send_media_group(self, *args, **kwargs):
        self.calls.append(("album", args, kwargs))

    async def copy_message(self, **kwargs):
        self.calls.append(("copy_message", kwargs))

    async def copy_messages(self, **kwargs):
        self.calls.append(("copy_messages", kwargs))

    async def send_message(self, *args, **kwargs):
        self.calls.append(("text", args, kwargs))


class FakePipeline:
    def __init__(self, *, error=None):
        self.error = error
        self.video_calls = []
        self.album_calls = []

    async def store_video_once(self, item):
        self.video_calls.append(item)
        if self.error:
            raise self.error
        return SimpleNamespace(chat_id=-100123, message_id=700, file_id="processed-video")

    async def store_album_once(self, group):
        self.album_calls.append(group)
        if self.error:
            raise self.error
        return SimpleNamespace(chat_id=-100123, message_ids=[701, 702], video_file_ids=["processed-video"])


def make_service():
    bot = FakeBot()
    ctx = SimpleNamespace(
        bot_id=101,
        bot=bot,
        bot_username="child_test_bot",
        settings=SimpleNamespace(
            admin_ids=frozenset({9001}),
            admin_feed_enabled_by_default=True,
        ),
        repositories=SimpleNamespace(admin_feed=SimpleNamespace(col=FakeAdminCollection())),
    )
    service = AdminFeedService(ctx)
    pipeline = FakePipeline()
    service.video_thumbnail_pipeline = pipeline
    return service, bot, pipeline


@pytest.mark.asyncio
async def test_admin_video_feed_copies_only_processed_buffer_video():
    service, bot, pipeline = make_service()
    await service.process({
        "sender_id": 123,
        "message_id": 456,
        "media_type": "video",
        "file_id": "original-video-id",
        "file_size": 10,
        "caption": "private publication text must not be sent",
    })

    assert len(pipeline.video_calls) == 1
    assert bot.calls == [("copy_message", {
        "chat_id": 9001, "from_chat_id": -100123, "message_id": 700, "protect_content": True,
    })]
    assert not any(call[0] == "video" for call in bot.calls)
    assert not any(call[0] == "text" for call in bot.calls)


@pytest.mark.asyncio
async def test_admin_video_feed_fails_closed_when_thumbnail_pipeline_fails():
    service, bot, pipeline = make_service()
    pipeline.error = RuntimeError("thumbnail pipeline offline")

    await service.process({"message_id": 8, "media_type": "video", "file_id": "original-video-id"})

    assert len(pipeline.video_calls) == 1
    assert bot.calls == []


@pytest.mark.asyncio
async def test_admin_video_album_copies_prepared_buffer_album_not_originals():
    service, bot, pipeline = make_service()
    await service.process({
        "sender_id": 123,
        "media_type": "album",
        "caption": "do not forward text",
        "group": {
            "media_group_id": "album-1",
            "items": [
                {"message_id": 1, "type": "photo", "file_id": "original-photo"},
                {"message_id": 2, "type": "video", "file_id": "original-video", "file_size": 10},
            ],
        },
    })

    assert len(pipeline.album_calls) == 1
    assert bot.calls == [("copy_messages", {
        "chat_id": 9001, "from_chat_id": -100123, "message_ids": [701, 702], "protect_content": True,
    })]
    assert not any(call[0] in {"video", "album"} for call in bot.calls)


@pytest.mark.asyncio
async def test_photo_album_without_videos_still_has_no_captions():
    service, bot, _ = make_service()
    await service.process({
        "sender_id": 123,
        "media_type": "album",
        "caption": "do not forward text",
        "group": {"items": [{"message_id": 1, "type": "photo", "file_id": "photo-id"}]},
    })

    assert len(bot.calls) == 1 and bot.calls[0][0] == "album"
    media = bot.calls[0][2]["media"]
    assert all(getattr(item, "caption", None) is None for item in media)
    assert not any(call[0] == "text" for call in bot.calls)
