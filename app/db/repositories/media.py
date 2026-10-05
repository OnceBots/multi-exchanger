from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ReturnDocument


class MediaRepository:
    def __init__(self, mongo) -> None:
        self.events = mongo.collection("media_events")
        self.groups = mongo.collection("media_groups")
        self.jobs = mongo.collection("broadcast_jobs")
        self.deliveries = mongo.collection("broadcast_deliveries")
        self.updates = mongo.collection("inbound_updates")

    async def register_update(self, bot_id: int, update_id: int, payload: dict) -> bool:
        try:
            await self.updates.insert_one({"bot_id": bot_id, "update_id": update_id, "payload": payload, "status": "PENDING", "created_at": datetime.now(timezone.utc)})
            return True
        except Exception as exc:
            if exc.__class__.__name__ == "DuplicateKeyError":
                existing = await self.updates.find_one({"bot_id": bot_id, "update_id": update_id})
                if existing and existing.get("status") != "PROCESSED":
                    await self.updates.update_one({"bot_id": bot_id, "update_id": update_id}, {"$set": {"payload": payload, "status": "PENDING", "updated_at": datetime.now(timezone.utc)}})
                    return True
                return False
            raise

    async def pending_updates(self, bot_id: int, limit: int = 500) -> list[dict]:
        return await self.updates.find({"bot_id": bot_id, "status": "PENDING"}).sort("created_at", 1).limit(limit).to_list(length=limit)

    async def mark_update(self, bot_id: int, update_id: int, status: str) -> None:
        await self.updates.update_one({"bot_id": bot_id, "update_id": update_id}, {"$set": {"status": status}})

    async def create_event(self, **doc) -> None:
        doc.setdefault("created_at", datetime.now(timezone.utc))
        await self.events.insert_one(doc)

    async def append_group(self, bot_id: int, room_id: str, media_group_id: str, item: dict, sender_id: int, source_chat_id: int, caption: str | None) -> None:
        await self.groups.update_one(
            {"bot_id": bot_id, "room_id": room_id, "media_group_id": media_group_id},
            {"$setOnInsert": {"bot_id": bot_id, "room_id": room_id, "media_group_id": media_group_id, "sender_id": sender_id, "source_chat_id": source_chat_id, "items": [], "caption": caption, "created_at": datetime.now(timezone.utc), "status": "COLLECTING"}, "$set": {"updated_at": datetime.now(timezone.utc)}, "$push": {"items": item}},
            upsert=True,
        )

    async def get_group(self, bot_id: int, room_id: str, media_group_id: str) -> dict | None:
        return await self.groups.find_one({"bot_id": bot_id, "room_id": room_id, "media_group_id": media_group_id})

    async def claim_group(self, bot_id: int, room_id: str, media_group_id: str) -> dict | None:
        return await self.groups.find_one_and_update(
            {"bot_id": bot_id, "room_id": room_id, "media_group_id": media_group_id, "status": {"$in": ["COLLECTING", "READY"]}},
            {"$set": {"status": "PROCESSING", "ready_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER,
        )

    async def create_job(self, job_id: str, bot_id: int, room_id: str, event_id: str, recipients_count: int) -> None:
        await self.jobs.insert_one({"bot_id": bot_id, "job_id": job_id, "room_id": room_id, "event_id": event_id, "recipients_count": recipients_count, "delivered_count": 0, "failed_count": 0, "status": "PROCESSING", "created_at": datetime.now(timezone.utc), "completed_at": None})

    async def add_delivery(self, bot_id: int, job_id: str, user_id: int, ok: bool, error: str | None = None) -> bool:
        try:
            await self.deliveries.insert_one({"bot_id": bot_id, "job_id": job_id, "user_id": user_id, "ok": ok, "error": error, "created_at": datetime.now(timezone.utc)})
        except Exception as exc:
            if exc.__class__.__name__ == "DuplicateKeyError":
                return False
            raise
        field = "delivered_count" if ok else "failed_count"
        await self.jobs.update_one({"bot_id": bot_id, "job_id": job_id}, {"$inc": {field: 1}})
        return True

    async def complete_job(self, bot_id: int, job_id: str) -> None:
        await self.jobs.update_one({"bot_id": bot_id, "job_id": job_id}, {"$set": {"status": "COMPLETED", "completed_at": datetime.now(timezone.utc)}})
