from __future__ import annotations


class RoomService:
    def __init__(self, room_repo, user_repo) -> None:
        self.room_repo = room_repo
        self.user_repo = user_repo

    async def create_room(self, bot_id: int, user_id: int, name: str, description: str, visibility: str = "PUBLIC", max_members: int = 100) -> dict:
        visibility = visibility.upper()
        if visibility not in {"PUBLIC", "PRIVATE"}:
            raise ValueError("visibility inválida")
        room = await self.room_repo.create(bot_id, user_id, name, description, visibility, max_members)
        await self.user_repo.set_active_room(bot_id, user_id, room["room_id"])
        return room

    async def join_room(self, bot_id: int, user_id: int, room_ref: str) -> dict | None:
        room = await self.room_repo.get(bot_id, room_ref) or await self.room_repo.find_by_invite(bot_id, room_ref)
        if not room:
            return None
        ok = await self.room_repo.join(bot_id, room["room_id"], user_id)
        if not ok:
            return None
        await self.user_repo.set_active_room(bot_id, user_id, room["room_id"])
        return room

    async def set_active_room(self, bot_id: int, user_id: int, room_id: str) -> bool:
        room = await self.room_repo.get(bot_id, room_id)
        if not room:
            return False
        await self.user_repo.set_active_room(bot_id, user_id, room_id)
        return True
