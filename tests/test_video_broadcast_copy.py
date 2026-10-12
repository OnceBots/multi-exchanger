from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.broadcast_service import BroadcastService


@pytest.mark.asyncio
async def test_buffer_video_uses_protected_copy_message():
    class FakeBot:
        def __init__(self):
            self.kwargs = None

        async def copy_message(self, **kwargs):
            self.kwargs = kwargs

    ctx = SimpleNamespace(bot_id=1, bot=FakeBot(), settings=SimpleNamespace(broadcast_concurrency_per_bot=1, broadcast_max_retries=1, storage_channel_id=""))
    service = BroadcastService(ctx)
    await service._send_with_retry(99, {
        "media_type": "video",
        "buffer_chat_id": -1001234567890,
        "buffer_message_id": 45,
    })
    assert ctx.bot.kwargs == {
        "chat_id": 99,
        "from_chat_id": -1001234567890,
        "message_id": 45,
        "protect_content": True,
    }


@pytest.mark.asyncio
async def test_buffer_album_uses_protected_copy_messages():
    class FakeBot:
        def __init__(self):
            self.kwargs = None

        async def copy_messages(self, **kwargs):
            self.kwargs = kwargs

    ctx = SimpleNamespace(
        bot_id=1,
        bot=FakeBot(),
        settings=SimpleNamespace(
            broadcast_concurrency_per_bot=1,
            broadcast_max_retries=1,
            storage_channel_id="",
        ),
    )
    service = BroadcastService(ctx)
    await service._send_album_with_retry(99, {
        "items": [{"message_id": 45, "type": "video", "file_id": "old-video"}],
        "buffer_chat_id": -1001234567890,
        "buffer_message_ids": [77, 78],
    })
    assert ctx.bot.kwargs == {
        "chat_id": 99,
        "from_chat_id": -1001234567890,
        "message_ids": [77, 78],
        "protect_content": True,
    }
