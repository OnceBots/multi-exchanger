from __future__ import annotations

from datetime import datetime


class SessionRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("sessions")

    async def get(self, bot_id: int, user_id: int) -> dict | None:
        return await self.col.find_one({"bot_id": bot_id, "user_id": user_id})

    async def set(self, bot_id: int, user_id: int, step: str, data: dict) -> None:
        await self.col.update_one({"bot_id": bot_id, "user_id": user_id}, {"$set": {"step": step, "data": data, "updated_at": datetime.utcnow()}, "$setOnInsert": {"bot_id": bot_id, "user_id": user_id}}, upsert=True)

    async def clear(self, bot_id: int, user_id: int) -> None:
        await self.col.delete_one({"bot_id": bot_id, "user_id": user_id})
