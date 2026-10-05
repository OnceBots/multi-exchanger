from __future__ import annotations

from app.core.datetime import utcnow


class UserRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("users")

    async def get(self, bot_id: int, user_id: int) -> dict | None:
        return await self.col.find_one({"bot_id": bot_id, "user_id": user_id}, {"_id": 0})

    async def upsert_from_telegram(self, bot_id: int, user) -> dict:
        data = {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "language_code": user.language_code,
            "updated_at": utcnow(),
        }
        await self.col.update_one(
            {"bot_id": bot_id, "user_id": user.id},
            {"$set": data, "$setOnInsert": {"bot_id": bot_id, "user_id": user.id, "created_at": utcnow()}},
            upsert=True,
        )
        return await self.get(bot_id, user.id) or {"bot_id": bot_id, "user_id": user.id, **data}

    async def count_for_bot(self, bot_id: int) -> int:
        return int(await self.col.count_documents({"bot_id": bot_id}))
