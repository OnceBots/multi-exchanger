import pytest
from app.services.room_service import RoomService


def test_default_room_media_policy_accepts_all_supported_attachments():
    policy = RoomService.default_media_policy()
    assert all(policy[key] for key in RoomService.MEDIA_POLICY_KEYS)
    room = {"settings": {"allow_video": False, "allow_photo": False, "allow_files": False}}
    for kind in ("video", "photo", "document", "animation", "audio", "voice", "video_note", "sticker"):
        assert RoomService.is_media_allowed(room, kind, policy)
    assert not RoomService.is_media_allowed(room, "unknown", policy)


def test_room_settings_cannot_override_an_admin_media_restriction():
    room = {"settings": {"allow_video": True, "allow_photo": True, "allow_files": True, "allow_animation": True, "allow_albums": True}}
    policy = {**RoomService.default_media_policy(), "allow_photo": False}
    assert not RoomService.is_media_allowed(room, "photo", policy)


def test_global_admin_policy_can_enable_other_types_on_existing_rooms():
    room = {"settings": {"allow_video": True}}
    policy = {**RoomService.default_media_policy(), "allow_photo": True, "allow_files": True}
    assert RoomService.is_media_allowed(room, "photo", policy)
    assert RoomService.is_media_allowed(room, "document", policy)


def test_unknown_and_missing_policy_values_use_new_supported_media_defaults():
    assert RoomService.normalize_media_policy({"allow_photo": False, "unexpected": True}) == {
        "allow_video": True,
        "allow_photo": False,
        "allow_files": True,
        "allow_animation": True,
        "allow_albums": True,
        "allow_audio": True,
        "allow_voice": True,
        "allow_video_note": True,
        "allow_sticker": True,
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
async def test_media_service_respects_explicit_admin_photo_restriction():
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
            return {"config": {"room_media_policy": {**RoomService.default_media_policy(), "allow_photo": False}, "room_media_policy_version": 2}}

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


@pytest.mark.asyncio
async def test_legacy_video_only_policy_is_migrated_once_to_all_supported_media():
    from types import SimpleNamespace
    from app.services.room_service import load_global_media_policy

    legacy = {
        "allow_video": True, "allow_photo": False, "allow_files": False,
        "allow_animation": False, "allow_albums": False, "allow_audio": False,
        "allow_voice": False, "allow_video_note": False, "allow_sticker": False,
    }
    class Bots:
        def __init__(self):
            self.config = {"room_media_policy": legacy}
            self.updates = []
        async def get(self, bot_id):
            return {"config": self.config}
        async def update_config(self, bot_id, updates):
            self.updates.append(updates)
            self.config.update(updates)

    bots = Bots()
    ctx = SimpleNamespace(bot_id=5, repositories=SimpleNamespace(bots=bots))
    policy = await load_global_media_policy(ctx)
    assert policy == RoomService.default_media_policy()
    assert bots.updates[-1]["room_media_policy_version"] == 2
    policy["allow_photo"] = False
    await bots.update_config(5, {"room_media_policy": policy, "room_media_policy_version": 2})
    assert not (await load_global_media_policy(ctx))["allow_photo"]


def test_document_as_video_uses_document_metadata():
    from types import SimpleNamespace
    from app.services.media_service import MediaService

    document = SimpleNamespace(file_id="file", file_size=734_003_200, file_name="large.mp4")
    message = SimpleNamespace(video=None, document=document)
    assert MediaService.media_object_from_message(message, "video") is document
    assert MediaService.media_object_from_message(message, "audio") is document
