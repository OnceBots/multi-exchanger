from __future__ import annotations

from app.core.enums import MemberRole, RoomVisibility


class RoomService:
    def __init__(self, repositories) -> None:
        self.repositories = repositories

    async def create_room(self, bot_id: int, owner_id: int, name: str, visibility: str, max_members: int, settings: dict, duration_minutes: int) -> dict:
        return await self.repositories.room.create(bot_id, owner_id, name.strip()[:80], visibility, max(2, min(max_members, 10000)), settings, max(0, duration_minutes))

    async def can_manage(self, bot_id: int, room_id: str, user_id: int) -> bool:
        member = await self.repositories.room.member(bot_id, room_id, user_id)
        return bool(member and member.get("role") in {MemberRole.OWNER.value, MemberRole.ADMIN.value})

    async def is_owner(self, bot_id: int, room_id: str, user_id: int) -> bool:
        member = await self.repositories.room.member(bot_id, room_id, user_id)
        return bool(member and member.get("role") == MemberRole.OWNER.value)

    async def join(self, bot_id: int, user_id: int, ref: str) -> tuple[bool, str, dict | None]:
        room = await self.repositories.room.resolve(bot_id, ref)
        if not room:
            return False, "No encontramos la sala.", None
        ok, message = await self.repositories.room.join(bot_id, room["room_id"], user_id)
        return ok, message, await self.repositories.room.get(bot_id, room["room_id"])

    async def leave(self, bot_id: int, room_id: str, user_id: int) -> tuple[bool, str]:
        return await self.repositories.room.leave(bot_id, room_id, user_id)

    @staticmethod
    def default_settings() -> dict:
        return {"allow_photo": True, "allow_video": True, "allow_files": True, "allow_animation": True, "allow_albums": True}

    @staticmethod
    def is_media_allowed(room: dict, media_type: str) -> bool:
        settings = room.get("settings") or {}
        key = {"photo": "allow_photo", "video": "allow_video", "document": "allow_files", "animation": "allow_animation"}.get(media_type)
        return bool(key is None or settings.get(key, True))

    @staticmethod
    def public_label(room: dict) -> str:
        return "🌎 Pública" if room.get("visibility") == RoomVisibility.PUBLIC.value else "🔒 Privada"
