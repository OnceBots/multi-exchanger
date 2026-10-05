from __future__ import annotations

from datetime import datetime, timezone


class BotRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("bots")

    async def create(self, document: dict) -> None:
        await self.col.insert_one(document)

    async def get(self, bot_id: int) -> dict | None:
        return await self.col.find_one({"bot_id": bot_id})

    async def list_enabled(self) -> list[dict]:
        return await self.col.find({"enabled": True}).sort("created_at", -1).to_list(length=None)

    async def list_all(self, limit: int = 500) -> list[dict]:
        return await self.col.find({}).sort("created_at", -1).limit(limit).to_list(length=limit)

    async def list_for_owner(self, owner_id: int, limit: int = 50) -> list[dict]:
        return await self.col.find({"owner_id": owner_id}).sort("created_at", -1).limit(limit).to_list(length=limit)

    async def count(self) -> int:
        return int(await self.col.count_documents({}))

    async def update_status(self, bot_id: int, status: str, last_error: str | None = None) -> None:
        data = {"status": status, "updated_at": datetime.now(timezone.utc), "last_error": last_error}
        await self.col.update_one({"bot_id": bot_id}, {"$set": data})

    async def mark_started(self, bot_id: int) -> None:
        now = datetime.now(timezone.utc)
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"status": "RUNNING", "last_started_at": now, "last_heartbeat": now, "last_error": None, "updated_at": now}})

    async def heartbeat(self, bot_id: int) -> None:
        now = datetime.now(timezone.utc)
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"last_heartbeat": now, "updated_at": now}})

    async def update_config(self, bot_id: int, config_updates: dict) -> None:
        now = datetime.now(timezone.utc)
        fields = {f"config.{key}": value for key, value in config_updates.items()}
        fields["updated_at"] = now
        await self.col.update_one({"bot_id": bot_id}, {"$set": fields})

    async def set_enabled(self, bot_id: int, enabled: bool) -> None:
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"enabled": enabled, "updated_at": datetime.now(timezone.utc)}})

    async def increment_restart(self, bot_id: int) -> None:
        await self.col.update_one({"bot_id": bot_id}, {"$inc": {"restart_count": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}})

    async def delete(self, bot_id: int) -> None:
        await self.col.delete_one({"bot_id": bot_id})
