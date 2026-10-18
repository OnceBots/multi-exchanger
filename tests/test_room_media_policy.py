import pytest
from app.services.room_service import RoomService


def test_default_room_media_policy_allows_only_individual_videos():
    policy = RoomService.default_media_policy()
    assert policy == {
        "allow_video": True,
        "allow_photo": False,
        "allow_files": False,
        "allow_animation": False,
        "allow_albums": False,
    }
    room = {"settings": {"allow_video": True, "allow_photo": True, "allow_files": True, "allow_animation": True, "allow_albums": True}}
    assert RoomService.is_media_allowed(room, "video", policy)
    assert not RoomService.is_media_allowed(room, "photo", policy)
    assert not RoomService.is_media_allowed(room, "document", policy)
    assert not RoomService.is_media_allowed(room, "animation", policy)


def test_legacy_room_settings_cannot_bypass_global_admin_policy():
    room = {"settings": {"allow_video": True, "allow_photo": True, "allow_files": True, "allow_animation": True, "allow_albums": True}}
    policy = RoomService.default_media_policy()
    assert not RoomService.is_media_allowed(room, "photo", policy)


def test_global_admin_policy_can_enable_other_types_on_existing_rooms():
    room = {"settings": {"allow_video": True}}
    policy = {**RoomService.default_media_policy(), "allow_photo": True, "allow_files": True}
    assert RoomService.is_media_allowed(room, "photo", policy)
    assert RoomService.is_media_allowed(room, "document", policy)


def test_unknown_and_missing_policy_values_default_to_video_only():
    assert RoomService.normalize_media_policy({"allow_photo": True, "unexpected": True}) == {
        "allow_video": True,
        "allow_photo": True,
        "allow_files": False,
        "allow_animation": False,
        "allow_albums": False,
    }
    assert not RoomService.is_media_allowed({}, "unknown", None)


class _FakeQueue:
    def __init__(self):
        self.items = []

    def qsize(self):
        return len(self.items)

    async def put(self, value):
        self.items.append(value)


@pytest.mark.asyncio
async def test_media_service_blocks_photo_despite_legacy_room_settings():
    from types import SimpleNamespace

    from app.services.media_service import MediaService

    class SessionRepository:
        async def get(self, bot_id, user_id):
            return {"data": {"active_room_id": "room-1"}}

    class RoomRepository:
        async def get(self, bot_id, room_id):
            return {
                "room_id": room_id,
                "status": "ACTIVE",
                "settings": {
                    "allow_video": True,
                    "allow_photo": True,
                    "allow_files": True,
                    "allow_animation": True,
                    "allow_albums": True,
                },
            }

        async def member(self, bot_id, room_id, user_id):
            return {"role": "MEMBER"}

    class BotRepository:
        async def get(self, bot_id):
            return {"config": {"room_media_policy": RoomService.default_media_policy()}}

    class FakeMessage:
        from_user = SimpleNamespace(id=77)
        chat = SimpleNamespace(id=123, type="private")
        message_id = 15
        media_group_id = None
        caption = None
        photo = [SimpleNamespace(file_id="photo-original")]
        video = None
        document = None
        animation = None

        def __init__(self):
            self.answers = []

        async def answer(self, value):
            self.answers.append(value)

    repo = SimpleNamespace(session=SessionRepository(), room=RoomRepository(), bots=BotRepository())
    broadcast_queue = _FakeQueue()
    ctx = SimpleNamespace(
        bot_id=22,
        repositories=repo,
        broadcast_queue=broadcast_queue,
        admin_feed_queue=None,
        album_queue=_FakeQueue(),
        task_registry=None,
        settings=SimpleNamespace(album_debounce_ms=100),
    )
    service = MediaService(ctx, RoomService(repo))
    message = FakeMessage()

    accepted = await service.handle_message(message)

    assert accepted is False
    assert not broadcast_queue.items
    assert len(message.answers) == 1
    assert "administradores" in message.answers[0]
