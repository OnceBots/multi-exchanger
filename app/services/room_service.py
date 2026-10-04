from __future__ import annotations

from datetime import datetime, timedelta, timezone


class RoomService:
    def __init__(self, room_repo, user_repo) -> None:
        self.room_repo = room_repo
        self.user_repo = user_repo

    async def create_room(
        self,
        bot_id: int,
        user_id: int,
        name: str,
        description: str,
        visibility: str = "PUBLIC",
        max_members: int = 100,
        settings: dict | None = None,
        duration_minutes: int | None = None,
    ) -> dict:
        room = await self.room_repo.create(
            bot_id,
            user_id,
            name,
            description,
            visibility,
            max_members,
            settings=settings,
            duration_minutes=duration_minutes,
        )
        await self.user_repo.set_active_room(bot_id, user_id, room["room_id"])
        return room

    async def join_room(self, bot_id: int, user_id: int, room_ref: str) -> dict | None:
        room = await self.room_repo.get(bot_id, room_ref.upper()) or await self.room_repo.find_by_invite(bot_id, room_ref)
        if not room:
            return None
        ok = await self.room_repo.join(bot_id, room["room_id"], user_id)
        if not ok:
            return None
        await self.user_repo.set_active_room(bot_id, user_id, room["room_id"])
        return await self.room_repo.get(bot_id, room["room_id"])

    async def leave_room(self, bot_id: int, user_id: int, room_id: str) -> bool:
        ok = await self.room_repo.leave(bot_id, room_id, user_id)
        if ok:
            user = await self.user_repo.get(bot_id, user_id)
            if user and user.get("active_room_id") == room_id:
                await self.user_repo.set_active_room(bot_id, user_id, None)
        return ok

    async def set_active_room(self, bot_id: int, user_id: int, room_id: str) -> bool:
        room = await self.room_repo.get(bot_id, room_id)
        if not room or room.get("status") not in {"ACTIVE", "PAUSED"}:
            return False
        membership = await self.room_repo.get_membership(bot_id, room_id, user_id)
        if not membership or membership.get("banned"):
            return False
        await self.user_repo.set_active_room(bot_id, user_id, room_id)
        return True

    async def can_manage(self, bot_id: int, room_id: str, user_id: int, global_admin: bool = False) -> bool:
        if global_admin:
            return True
        member = await self.room_repo.get_membership(bot_id, room_id, user_id)
        return bool(member and member.get("role") in {"OWNER", "ADMIN"} and not member.get("banned"))

    async def build_share_url(self, bot_username: str | None, invite_code: str) -> str:
        if not bot_username:
            return invite_code
        return f"https://t.me/{bot_username}?start=join_{invite_code}"

    @staticmethod
    def duration_label(duration_minutes: int | None) -> str:
        if not duration_minutes or duration_minutes <= 0:
            return "Sin expiración"
        if duration_minutes < 60:
            return f"{duration_minutes} min"
        if duration_minutes % 60 == 0:
            hours = duration_minutes // 60
            if hours < 24:
                return f"{hours} h"
        return f"{duration_minutes} min"
