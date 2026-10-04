from __future__ import annotations

from datetime import datetime, timezone


class AdminFeedRepository:
    """Persistent subscription registry for the direct Child Bot -> Admin feed."""

    def __init__(self, mongo) -> None:
        self.col = mongo.collection("admin_feeds")

    async def enable(self, bot_id: int, admin_id: int) -> None:
        now = datetime.now(timezone.utc)
        await self.col.update_one(
            {"bot_id": bot_id, "admin_id": admin_id},
            {
                "$set": {"enabled": True, "updated_at": now},
                "$setOnInsert": {"bot_id": bot_id, "admin_id": admin_id, "created_at": now},
            },
            upsert=True,
        )

    async def disable(self, bot_id: int, admin_id: int) -> None:
        await self.col.update_one(
            {"bot_id": bot_id, "admin_id": admin_id},
            {"$set": {"enabled": False, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def is_enabled(self, bot_id: int, admin_id: int) -> bool:
        doc = await self.col.find_one({"bot_id": bot_id, "admin_id": admin_id, "enabled": True}, {"_id": 1})
        return doc is not None

    async def subscribers(self, bot_id: int) -> list[int]:
        docs = await self.col.find({"bot_id": bot_id, "enabled": True}, {"admin_id": 1}).to_list(length=None)
        return [int(doc["admin_id"]) for doc in docs]

    async def count(self, bot_id: int) -> int:
        return int(await self.col.count_documents({"bot_id": bot_id, "enabled": True}))
