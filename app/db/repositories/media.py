from __future__ import annotations

import secrets
from datetime import timedelta

from app.core.datetime import utcnow

_UPDATE_PROCESSING_LEASE = timedelta(minutes=10)


class MediaRepository:
    def __init__(self, mongo) -> None:
        self.events = mongo.collection("media_events")
        self.groups = mongo.collection("media_groups")
        self.jobs = mongo.collection("broadcast_jobs")
        self.deliveries = mongo.collection("broadcast_deliveries")
        self.updates = mongo.collection("inbound_updates")

    async def claim_update(self, bot_id: int, update_id: int, payload: dict) -> str | None:
        """Atomically claim a Telegram update, rejecting active or completed duplicates.

        A lease makes interrupted PROCESSING records recoverable. The claim token
        prevents a worker whose lease expired from overwriting a newer worker's
        result when it eventually finishes.
        """
        bot_id = int(bot_id)
        update_id = int(update_id)
        now = utcnow()
        token = secrets.token_urlsafe(24)
        lease_until = now + _UPDATE_PROCESSING_LEASE
        document = {
            "bot_id": bot_id,
            "update_id": update_id,
            "payload": payload,
            "status": "PROCESSING",
            "claim_token": token,
            "lease_until": lease_until,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self.updates.insert_one(document)
            return token
        except Exception as exc:
            if exc.__class__.__name__ != "DuplicateKeyError":
                raise

        # Legacy PENDING/FAILED rows and PROCESSING rows with an expired/missing
        # lease are eligible. This filter + update is atomic across app workers.
        from pymongo import ReturnDocument

        claimed = await self.updates.find_one_and_update(
            {
                "bot_id": bot_id,
                "update_id": update_id,
                "$or": [
                    {"status": {"$in": ["PENDING", "FAILED"]}},
                    {"status": "PROCESSING", "lease_until": {"$lte": now}},
                    {"status": "PROCESSING", "lease_until": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "payload": payload,
                    "status": "PROCESSING",
                    "claim_token": token,
                    "lease_until": lease_until,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            return None
        return str(claimed.get("claim_token") or token)

    async def register_update(self, bot_id: int, update_id: int, payload: dict) -> bool:
        """Compatibility wrapper retained for callers that only need a yes/no result."""
        return bool(await self.claim_update(bot_id, update_id, payload))

    async def pending_updates(self, bot_id: int, limit: int = 500) -> list[dict]:
        now = utcnow()
        query = {
            "bot_id": int(bot_id),
            "$or": [
                {"status": {"$in": ["PENDING", "FAILED"]}},
                {"status": "PROCESSING", "lease_until": {"$lte": now}},
                {"status": "PROCESSING", "lease_until": {"$exists": False}},
            ],
        }
        return await self.updates.find(query).sort("created_at", 1).limit(limit).to_list(length=limit)

    async def mark_update(
        self, bot_id: int, update_id: int, status: str, *, claim_token: str | None = None
    ) -> None:
        now = utcnow()
        query = {"bot_id": int(bot_id), "update_id": int(update_id)}
        if claim_token:
            query["claim_token"] = str(claim_token)
        update = {
            "$set": {
                "status": str(status),
                "updated_at": now,
                "lease_until": None,
                "claim_token": None,
            }
        }
        if str(status) in {"PROCESSED", "SKIPPED"}:
            update["$set"]["processed_at"] = now
        await self.updates.update_one(query, update)

    async def create_event(self, event_key: str, bot_id: int, room_id: str, sender_id: int, message_id: int, media_type: str, file_id: str, caption: str | None) -> dict:
        doc = {"event_key": event_key, "bot_id": bot_id, "room_id": room_id, "sender_id": sender_id, "message_id": message_id, "media_type": media_type, "file_id": file_id, "caption": caption, "created_at": utcnow()}
        try:
            await self.events.insert_one(doc)
        except Exception as exc:
            if exc.__class__.__name__ != "DuplicateKeyError":
                raise
        return doc

    async def append_group(self, bot_id: int, room_id: str, media_group_id: str, item: dict, sender_id: int, caption: str | None) -> dict:
        now = utcnow()
        await self.groups.update_one(
            {"bot_id": bot_id, "room_id": room_id, "media_group_id": media_group_id},
            {"$setOnInsert": {"bot_id": bot_id, "room_id": room_id, "media_group_id": media_group_id, "sender_id": sender_id, "caption": caption, "created_at": now, "status": "COLLECTING"}, "$set": {"updated_at": now}, "$push": {"items": item}},
            upsert=True,
        )
        return await self.groups.find_one({"bot_id": bot_id, "room_id": room_id, "media_group_id": media_group_id}) or {}

    async def claim_group(self, bot_id: int, room_id: str, media_group_id: str) -> dict | None:
        from pymongo import ReturnDocument
        return await self.groups.find_one_and_update(
            {"bot_id": bot_id, "room_id": room_id, "media_group_id": media_group_id, "status": "COLLECTING"},
            {"$set": {"status": "PROCESSING", "ready_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )

    async def create_job(self, bot_id: int, job_id: str, room_id: str, event_id: str, recipients_count: int) -> None:
        await self.jobs.insert_one({"bot_id": bot_id, "job_id": job_id, "room_id": room_id, "event_id": event_id, "recipients_count": recipients_count, "delivered_count": 0, "failed_count": 0, "status": "PROCESSING", "created_at": utcnow(), "completed_at": None})

    async def add_delivery(self, bot_id: int, job_id: str, user_id: int, ok: bool, error: str | None = None) -> None:
        try:
            await self.deliveries.insert_one({"bot_id": bot_id, "job_id": job_id, "user_id": user_id, "ok": ok, "error": error, "created_at": utcnow()})
        except Exception as exc:
            if exc.__class__.__name__ == "DuplicateKeyError":
                return
            raise
        field = "delivered_count" if ok else "failed_count"
        await self.jobs.update_one({"bot_id": bot_id, "job_id": job_id}, {"$inc": {field: 1}})

    async def complete_job(self, bot_id: int, job_id: str, status: str = "COMPLETED") -> None:
        await self.jobs.update_one({"bot_id": bot_id, "job_id": job_id}, {"$set": {"status": status, "completed_at": utcnow()}})
