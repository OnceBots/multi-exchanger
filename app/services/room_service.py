from __future__ import annotations

from app.core.enums import MemberRole, RoomVisibility


class RoomService:
    def __init__(self, repositories) -> None:
        self.repositories = repositories

    async def create_room(self, bot_id: int, owner_id: int, visibility: str, max_members: int, settings: dict, duration_minutes: int, password: str | None = None) -> dict:
        max_members = max(2, min(max_members, 10000))
        if visibility == RoomVisibility.PRIVATE.value:
            password = (password or "").strip()
            if len(password) < 4:
                raise ValueError("Las salas privadas requieren una contraseña de al menos 4 caracteres.")
        else:
            password = None
        return await self.repositories.room.create(bot_id, owner_id, visibility, max_members, settings, max(0, duration_minutes), password)

    async def can_manage(self, bot_id: int, room_id: str, user_id: int) -> bool:
        member = await self.repositories.room.member(bot_id, room_id, user_id)
        return bool(member and member.get("role") in {MemberRole.OWNER.value, MemberRole.ADMIN.value})

    async def is_owner(self, bot_id: int, room_id: str, user_id: int) -> bool:
        member = await self.repositories.room.member(bot_id, room_id, user_id)
        return bool(member and member.get("role") == MemberRole.OWNER.value)

    async def join(self, bot_id: int, user_id: int, ref: str, password: str | None = None) -> tuple[bool, str, dict | None]:
        room = await self.repositories.room.resolve(bot_id, ref)
        if not room:
            return False, "No encontramos la sala.", None
        ok, message = await self.repositories.room.join(bot_id, room["room_id"], user_id, password=password)
        return ok, message, await self.repositories.room.get(bot_id, room["room_id"])

    async def leave(self, bot_id: int, room_id: str, user_id: int) -> tuple[bool, str]:
        return await self.repositories.room.leave(bot_id, room_id, user_id)

    MEDIA_POLICY_KEYS = ("allow_video", "allow_photo", "allow_files", "allow_animation", "allow_albums")

    @staticmethod
    def default_media_policy() -> dict:
        """Safe bot-wide policy: rooms publish videos only until a bot admin opts in."""
        return {
            "allow_video": True,
            "allow_photo": False,
            "allow_files": False,
            "allow_animation": False,
            "allow_albums": False,
        }

    @classmethod
    def normalize_media_policy(cls, value: dict | None) -> dict:
        defaults = cls.default_media_policy()
        value = value if isinstance(value, dict) else {}
        return {key: bool(value.get(key, defaults[key])) for key in cls.MEDIA_POLICY_KEYS}

    @classmethod
    def default_settings(cls) -> dict:
        # Retained in room documents for compatibility. New rooms inherit the
        # current bot-wide media policy; authorization is still enforced globally.
        return cls.default_media_policy()

    @classmethod
    def is_media_allowed(cls, room: dict, media_type: str, policy: dict | None = None) -> bool:
        """Apply the bot-admin-controlled policy; legacy room settings never bypass it."""
        policy = cls.normalize_media_policy(policy)
        key = {"photo": "allow_photo", "video": "allow_video", "document": "allow_files", "animation": "allow_animation"}.get(media_type)
        if key is None or not policy.get(key, False):
            return False
        return True

    @staticmethod
    def public_label(room: dict) -> str:
        return "🌎 Pública" if room.get("visibility") == RoomVisibility.PUBLIC.value else "🔒 Privada"
