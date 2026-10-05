from __future__ import annotations

from app.core.datetime import utcnow


class SessionRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("sessions")

    async def get(self, bot_id: int, user_id: int) -> dict | None:
        return await self.col.find_one({"bot_id": bot_id, "user_id": user_id})

    async def set(self, bot_id: int, user_id: int, step: str, data: dict) -> None:
        now = utcnow()
        await self.col.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$set": {"step": step, "data": data, "updated_at": now}, "$setOnInsert": {"bot_id": bot_id, "user_id": user_id, "created_at": now}},
            upsert=True,
        )

    async def clear(self, bot_id: int, user_id: int) -> None:
        await self.col.delete_one({"bot_id": bot_id, "user_id": user_id})

    async def patch_data(self, bot_id: int, user_id: int, **updates) -> dict:
        current = await self.get(bot_id, user_id) or {"data": {}}
        data = dict(current.get("data") or {})
        data.update(updates)
        await self.set(bot_id, user_id, current.get("step", ""), data)
        return data
