from __future__ import annotations

from app.core.datetime import utcnow


class AdminFeedRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("admin_feeds")

    async def is_enabled(self, bot_id: int, admin_id: int) -> bool:
        doc = await self.col.find_one({"bot_id": bot_id, "admin_id": admin_id})
        return bool(doc and doc.get("enabled", False))

    async def set_enabled(self, bot_id: int, admin_id: int, enabled: bool) -> None:
        await self.col.update_one({"bot_id": bot_id, "admin_id": admin_id}, {"$set": {"enabled": bool(enabled), "updated_at": utcnow()}, "$setOnInsert": {"bot_id": bot_id, "admin_id": admin_id, "created_at": utcnow()}}, upsert=True)

    async def enabled_admins(self, bot_id: int) -> list[int]:
        docs = await self.col.find({"bot_id": bot_id, "enabled": True}).to_list(length=None)
        return [int(item["admin_id"]) for item in docs]
