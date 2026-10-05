from __future__ import annotations

from datetime import datetime, timezone


class UserRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("users")

    async def upsert(self, bot_id: int, user_id: int, **extra) -> None:
        extra.update({"updated_at": datetime.now(timezone.utc)})
        await self.col.update_one({"bot_id": bot_id, "user_id": user_id}, {"$set": extra, "$setOnInsert": {"bot_id": bot_id, "user_id": user_id, "created_at": datetime.now(timezone.utc)}}, upsert=True)

    async def get(self, bot_id: int, user_id: int) -> dict | None:
        return await self.col.find_one({"bot_id": bot_id, "user_id": user_id})

    async def set_active_room(self, bot_id: int, user_id: int, room_id: str | None) -> None:
        await self.upsert(bot_id, user_id, active_room_id=room_id)
