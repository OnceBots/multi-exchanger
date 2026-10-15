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

    async def send_message(self, *args, **kwargs):
        self.calls.append(("text", args, kwargs))


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
        repositories=SimpleNamespace(
            admin_feed=SimpleNamespace(col=FakeAdminCollection())
        ),
    )
    return AdminFeedService(ctx), bot


@pytest.mark.asyncio
async def test_admin_video_feed_has_no_caption_or_companion_text():
    service, bot = make_service()
    await service.process({
        "sender_id": 123,
        "media_type": "video",
        "file_id": "video-id",
        "caption": "private publication text must not be sent",
    })

    assert bot.calls == [("video", (9001, "video-id"), {"caption": None})]
    assert not any(call[0] == "text" for call in bot.calls)


@pytest.mark.asyncio
async def test_admin_album_feed_strips_every_caption():
    service, bot = make_service()
    await service.process({
        "sender_id": 123,
        "media_type": "album",
        "caption": "do not forward text",
        "group": {
            "items": [
                {"message_id": 1, "type": "photo", "file_id": "photo-id"},
                {"message_id": 2, "type": "video", "file_id": "video-id"},
            ]
        },
    })

    assert len(bot.calls) == 1 and bot.calls[0][0] == "album"
    media = bot.calls[0][2]["media"]
    assert all(getattr(item, "caption", None) is None for item in media)
    assert not any(call[0] == "text" for call in bot.calls)
