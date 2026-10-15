from __future__ import annotations

from app.core.datetime import utcnow


class BotRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("bots")

    async def create(self, document: dict) -> None:
        await self.col.insert_one(document)

    async def get(self, bot_id: int) -> dict | None:
        return await self.col.find_one({"bot_id": int(bot_id)})

    async def exists(self, bot_id: int) -> bool:
        return bool(await self.col.find_one({"bot_id": int(bot_id)}, {"_id": 1}))

    async def list_enabled(self) -> list[dict]:
        return await self.col.find({"enabled": True}).sort("created_at", 1).to_list(length=None)

    async def list_all(self, limit: int = 500) -> list[dict]:
        return await self.col.find({}).sort("created_at", -1).limit(limit).to_list(length=limit)

    async def list_for_owner(self, owner_id: int, limit: int = 50) -> list[dict]:
        return await self.col.find({"owner_id": int(owner_id)}).sort("created_at", -1).limit(limit).to_list(length=limit)

    async def mark_starting(self, bot_id: int) -> None:
        now = utcnow()
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"status": "STARTING", "updated_at": now, "last_error": None}})

    async def mark_running(self, bot_id: int) -> None:
        now = utcnow()
        await self.col.update_one(
            {"bot_id": bot_id},
            {"$set": {"status": "RUNNING", "last_started_at": now, "last_heartbeat": now, "updated_at": now, "last_error": None}},
        )

    async def update_status(self, bot_id: int, status: str, error: str | None = None) -> None:
        await self.col.update_one(
            {"bot_id": bot_id},
            {"$set": {"status": status, "last_error": error, "updated_at": utcnow()}},
        )

    async def heartbeat(self, bot_id: int) -> None:
        now = utcnow()
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"last_heartbeat": now, "updated_at": now}})

    async def increment_restart(self, bot_id: int) -> None:
        await self.col.update_one({"bot_id": bot_id}, {"$inc": {"restart_count": 1}, "$set": {"updated_at": utcnow()}})

    async def set_enabled(self, bot_id: int, enabled: bool) -> None:
        await self.col.update_one({"bot_id": bot_id}, {"$set": {"enabled": bool(enabled), "updated_at": utcnow()}})

    async def update_config(self, bot_id: int, updates: dict) -> None:
        fields = {f"config.{key}": value for key, value in updates.items()}
        fields["updated_at"] = utcnow()
        await self.col.update_one({"bot_id": bot_id}, {"$set": fields})


    async def set_lifecycle(self, bot_id: int, lifecycle: dict) -> None:
        await self.col.update_one(
            {"bot_id": int(bot_id)},
            {"$set": {"config.lifecycle": lifecycle, "updated_at": utcnow()}},
        )

    async def clear_lifecycle(self, bot_id: int) -> None:
        await self.col.update_one(
            {"bot_id": int(bot_id)},
            {"$unset": {"config.lifecycle": ""}, "$set": {"updated_at": utcnow()}},
        )

    async def archive(self, bot_id: int, reason: str) -> None:
        now = utcnow()
        await self.col.update_one(
            {"bot_id": int(bot_id)},
            {"$set": {
                "enabled": False,
                "status": "ARCHIVED",
                "updated_at": now,
                "archived_at": now,
                "config.lifecycle.mode": "ARCHIVED",
                "config.lifecycle.reason": reason,
                "config.lifecycle.archived_at": now,
            }},
        )


    async def reactivate_archived(self, bot_id: int, updates: dict) -> bool:
        """Atomically reactivate an archived bot without replacing its config/data.

        ``updates`` should contain only refreshed identity/secrets/runtime fields.
        Existing owner, created_at, config preferences and related collections are
        intentionally preserved.
        """
        lifecycle_fields = {
            "config.lifecycle.mode": "",
            "config.lifecycle.reason": "",
            "config.lifecycle.archived_at": "",
            "config.lifecycle.delete_at": "",
            "config.lifecycle.scheduled_at": "",
            "config.lifecycle.scheduled_by": "",
            "config.lifecycle.label": "",
            "archived_at": "",
        }
        now = utcnow()
        changes = dict(updates)
        changes["bot_id"] = int(bot_id)
        changes["enabled"] = True
        changes["status"] = "STARTING"
        changes.setdefault("updated_at", now)
        result = await self.col.update_one(
            {
                "bot_id": int(bot_id),
                "$or": [
                    {"status": "ARCHIVED"},
                    {"config.lifecycle.mode": "ARCHIVED"},
                ],
            },
            {"$set": changes, "$unset": lifecycle_fields},
        )
        return bool(result.matched_count == 1)

    async def delete(self, bot_id: int) -> None:
        await self.col.delete_one({"bot_id": int(bot_id)})
