from __future__ import annotations

from datetime import datetime


class BotRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("bots")

    async def create(self, document: dict) -> None:
        await self.col.insert_one(document)

    async def get(self, bot_id: int) -> dict | None:
        return await self.col.find_one({"bot_id": bot_id})

    async def list_enabled(self) -> list[dict]:
        return await self.col.find({"enabled": True}).to_list(length=None)

    async def list_all(self) -> list[dict]:
        return await self.col.find({}).sort("created_at", -1).to_list(length=None)

    async def update_status(self, bot_id: int, status: str, last_error: str | None = None) -> None:
        data = {"status": status, "updated_at": datetime.utcnow(), "last_error": last_error}
        await self.col.update_one({"bot_id": bot_id}, {"$set": data})

    async def mark_started(self, bot_id: int) -> None:
        now = datetime.utcnow()
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"status": "RUNNING", "last_started_at": now, "last_heartbeat": now, "last_error": None, "updated_at": now}})

    async def heartbeat(self, bot_id: int) -> None:
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"last_heartbeat": datetime.utcnow(), "updated_at": datetime.utcnow()}})

    async def set_enabled(self, bot_id: int, enabled: bool) -> None:
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"enabled": enabled, "updated_at": datetime.utcnow()}})

    async def increment_restart(self, bot_id: int) -> None:
        await self.col.update_one({"bot_id": bot_id}, {"$inc": {"restart_count": 1}, "$set": {"updated_at": datetime.utcnow()}})

    async def delete(self, bot_id: int) -> None:
        await self.col.delete_one({"bot_id": bot_id})
