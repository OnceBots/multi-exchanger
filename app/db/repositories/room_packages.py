from __future__ import annotations

import secrets
from datetime import timedelta

from app.core.datetime import utcnow


class RoomPackageRepository:
    """Durable metadata for per-room encrypted media packages."""

    def __init__(self, mongo) -> None:
        self.packages = mongo.collection("room_media_packages")
        self.items = mongo.collection("room_media_package_items")
        self.deliveries = mongo.collection("room_media_package_deliveries")

    async def get_open_package(self, bot_id: int, room_id: str, sender_id: int | None = None) -> dict | None:
        query = {"bot_id": int(bot_id), "room_id": str(room_id), "state": "OPEN"}
        # New packages are isolated per sender. Omitting sender_id remains available
        # for legacy administrative tooling, but new intake always supplies it.
        if sender_id is not None:
            query["sender_id"] = int(sender_id)
        return await self.packages.find_one(query, {"_id": 0})

    async def get_package(self, bot_id: int, package_id: str) -> dict | None:
        return await self.packages.find_one(
            {"bot_id": int(bot_id), "package_id": str(package_id)}, {"_id": 0}
        )

    async def create_package(self, bot_id: int, room_id: str, sender_id: int | None = None) -> dict:
        now = utcnow()
        # ID contains no user/token data and is safe to place in callback_data.
        for _ in range(4):
            package_id = secrets.token_hex(8)
            doc = {
                "bot_id": int(bot_id),
                "room_id": str(room_id),
                **({"sender_id": int(sender_id)} if sender_id is not None else {}),
                "package_id": package_id,
                "state": "OPEN",
                "created_at": now,
                "updated_at": now,
                "last_item_at": now,
                "item_count": 0,
                "staged_count": 0,
                "pending_count": 0,
                "failed_count": 0,
                "duplicate_count": 0,
                "reserved_bytes": 0,
                "staged_bytes": 0,
                "status_message_ids": {},
                "delivery_user_ids": [],
                "delivery_results": {},
            }
            try:
                await self.packages.insert_one(doc)
                return doc
            except Exception as exc:
                if exc.__class__.__name__ != "DuplicateKeyError":
                    raise
        raise RuntimeError("No se pudo generar un ID único para el paquete multimedia.")

    async def add_item(self, bot_id: int, item: dict) -> bool:
        """Insert once; return False for a duplicate Telegram message/update."""
        now = utcnow()
        document = {**item, "bot_id": int(bot_id), "created_at": now, "updated_at": now}
        try:
            await self.items.insert_one(document)
        except Exception as exc:
            if exc.__class__.__name__ != "DuplicateKeyError":
                raise
            return False
        expected = max(0, int(item.get("expected_size") or 0))
        await self.packages.update_one(
            {"bot_id": int(bot_id), "package_id": item["package_id"]},
            {
                "$inc": {"item_count": 1, "pending_count": 1, "reserved_bytes": expected},
                "$set": {"last_item_at": now, "updated_at": now},
            },
        )
        return True

    async def get_item(self, bot_id: int, item_id: str) -> dict | None:
        return await self.items.find_one(
            {"bot_id": int(bot_id), "item_id": str(item_id)}, {"_id": 0}
        )

    async def reconcile_package_counters(self, bot_id: int, package_id: str) -> None:
        """Repair counters from item status after an interrupted multi-document update.

        Package and item records are separate MongoDB documents. Recomputing these
        derived counters makes restarts safe even if a process dies between changing
        an item's status and updating its package summary.
        """
        pipeline = [
            {"$match": {"bot_id": int(bot_id), "package_id": str(package_id)}},
            {"$group": {
                "_id": None,
                "item_count": {"$sum": {"$cond": [{"$not": [{"$in": ["$status", ["FAILED", "DUPLICATE"]]}]}, 1, 0]}},
                "pending_count": {"$sum": {"$cond": [{"$in": ["$status", ["QUEUED", "DOWNLOADING"]]}, 1, 0]}},
                "staged_count": {"$sum": {"$cond": [{"$in": ["$status", ["STAGED", "STAGED_DELETE_PENDING"]]}, 1, 0]}},
                "failed_count": {"$sum": {"$cond": [{"$eq": ["$status", "FAILED"]}, 1, 0]}},
                "duplicate_count": {"$sum": {"$cond": [{"$eq": ["$status", "DUPLICATE"]}, 1, 0]}},
                "staged_bytes": {"$sum": {"$cond": [{"$in": ["$status", ["STAGED", "STAGED_DELETE_PENDING"]]}, {"$ifNull": ["$staged_size", 0]}, 0]}},
                "reserved_bytes": {"$sum": {"$cond": [
                    {"$in": ["$status", ["STAGED", "STAGED_DELETE_PENDING"]]},
                    {"$ifNull": ["$staged_size", 0]},
                    {"$cond": [{"$in": ["$status", ["QUEUED", "DOWNLOADING"]]}, {"$ifNull": ["$expected_size", 0]}, 0]},
                ]}},
            }},
        ]
        cursor = await self.items.aggregate(pipeline)
        rows = await cursor.to_list(length=1)
        counters = rows[0] if rows else {
            "item_count": 0, "pending_count": 0, "staged_count": 0,
            "failed_count": 0, "duplicate_count": 0, "staged_bytes": 0, "reserved_bytes": 0,
        }
        counters.pop("_id", None)
        await self.packages.update_one(
            {"bot_id": int(bot_id), "package_id": str(package_id)},
            {"$set": {**{key: int(counters.get(key, 0) or 0) for key in (
                "item_count", "pending_count", "staged_count", "failed_count", "duplicate_count", "staged_bytes", "reserved_bytes"
            )}, "counters_reconciled_at": utcnow(), "updated_at": utcnow()}},
        )

    async def list_pending_items(self, bot_id: int, limit: int = 250) -> list[dict]:
        query = {
            "bot_id": int(bot_id),
            "status": {"$in": ["QUEUED", "DOWNLOADING", "STAGED_DELETE_PENDING"]},
        }
        return await self.items.find(query, {"_id": 0}).sort("created_at", 1).limit(limit).to_list(length=limit)

    async def mark_downloading(self, bot_id: int, item_id: str) -> None:
        await self.items.update_one(
            {"bot_id": int(bot_id), "item_id": str(item_id), "status": {"$in": ["QUEUED", "DOWNLOADING"]}},
            {"$set": {"status": "DOWNLOADING", "updated_at": utcnow()}},
        )

    async def update_item_package(self, bot_id: int, item_id: str, package_id: str, expected_size: int) -> None:
        await self.items.update_one(
            {"bot_id": int(bot_id), "item_id": str(item_id), "status": "DOWNLOADING"},
            {"$set": {"package_id": str(package_id), "expected_size": int(expected_size), "updated_at": utcnow()}},
        )

    async def reassign_item(self, bot_id: int, item: dict, new_package: dict, actual_size: int) -> dict:
        old_package_id = str(item["package_id"])
        expected = max(0, int(item.get("expected_size") or 0))
        await self.packages.update_one(
            {"bot_id": int(bot_id), "package_id": old_package_id},
            {"$inc": {"item_count": -1, "pending_count": -1, "reserved_bytes": -expected}, "$set": {"updated_at": utcnow()}},
        )
        await self.packages.update_one(
            {"bot_id": int(bot_id), "package_id": new_package["package_id"]},
            {"$inc": {"item_count": 1, "pending_count": 1, "reserved_bytes": int(actual_size)}, "$set": {"last_item_at": utcnow(), "updated_at": utcnow()}},
        )
        await self.update_item_package(bot_id, item["item_id"], new_package["package_id"], actual_size)
        updated = dict(item)
        updated["package_id"] = new_package["package_id"]
        updated["expected_size"] = int(actual_size)
        return updated

    async def find_staged_duplicate(self, bot_id: int, room_id: str, sender_id: int, content_sha256: str) -> dict | None:
        return await self.items.find_one(
            {
                "bot_id": int(bot_id), "room_id": str(room_id), "sender_id": int(sender_id),
                "media_type": "video", "content_sha256": str(content_sha256),
                "status": {"$in": ["STAGED", "STAGED_DELETE_PENDING"]},
            },
            {"_id": 0},
        )

    async def mark_item_duplicate(self, bot_id: int, item: dict, duplicate_of: str, content_sha256: str) -> bool:
        expected = max(0, int(item.get("expected_size") or 0))
        result = await self.items.update_one(
            {"bot_id": int(bot_id), "item_id": str(item["item_id"]), "status": "DOWNLOADING"},
            {"$set": {
                "status": "DUPLICATE", "duplicate_of_item_id": str(duplicate_of),
                "duplicate_sha256": str(content_sha256), "updated_at": utcnow(),
                "duplicate_detected_at": utcnow(), "source_deleted": False,
                "source_delete_attempted": False,
            }},
        )
        if not result.modified_count:
            current = await self.get_item(bot_id, str(item["item_id"]))
            return bool(current and current.get("status") == "DUPLICATE")
        await self.packages.update_one(
            {"bot_id": int(bot_id), "package_id": str(item["package_id"])},
            {"$inc": {
                "item_count": -1, "pending_count": -1, "reserved_bytes": -expected,
                "duplicate_count": 1,
            }, "$set": {"updated_at": utcnow()}},
        )
        return True

    async def mark_duplicate_source_deleted(self, bot_id: int, item_id: str, *, deleted: bool, error: str | None = None) -> None:
        await self.items.update_one(
            {"bot_id": int(bot_id), "item_id": str(item_id), "status": "DUPLICATE"},
            {"$set": {
                "source_deleted": bool(deleted), "source_delete_attempted": True,
                "source_delete_error": str(error)[:300] if error else None,
                "updated_at": utcnow(),
            }},
        )

    async def count_recent_user_videos(self, bot_id: int, room_id: str, sender_id: int, since) -> int:
        return int(await self.items.count_documents({
            "bot_id": int(bot_id), "room_id": str(room_id), "sender_id": int(sender_id),
            "media_type": "video", "status": {"$in": ["STAGED", "STAGED_DELETE_PENDING"]},
            "staged_at": {"$gte": since}, "content_sha256": {"$type": "string"},
        }))

    async def mark_staged(self, bot_id: int, item: dict, path: str, actual_size: int, content_sha256: str | None = None) -> bool:
        expected = max(0, int(item.get("expected_size") or 0))
        result = await self.items.update_one(
            {"bot_id": int(bot_id), "item_id": item["item_id"], "status": "DOWNLOADING"},
            {"$set": {
                "status": "STAGED_DELETE_PENDING",
                "staged_path": str(path),
                "staged_size": int(actual_size),
                "staged_at": utcnow(),
                **({"content_sha256": str(content_sha256)} if content_sha256 else {}),
                "updated_at": utcnow(),
            }},
        )
        if not result.modified_count:
            existing = await self.get_item(bot_id, item["item_id"])
            return bool(existing and existing.get("status") in {"STAGED_DELETE_PENDING", "STAGED"})
        await self.packages.update_one(
            {"bot_id": int(bot_id), "package_id": item["package_id"]},
            {
                "$inc": {
                    "pending_count": -1,
                    "staged_count": 1,
                    "staged_bytes": int(actual_size),
                    "reserved_bytes": int(actual_size) - expected,
                },
                "$set": {"updated_at": utcnow()},
            },
        )
        return True

    async def mark_source_deleted(self, bot_id: int, item_id: str, *, deleted: bool, error: str | None = None) -> None:
        await self.items.update_one(
            {"bot_id": int(bot_id), "item_id": str(item_id)},
            {"$set": {
                "source_deleted": bool(deleted),
                "source_delete_attempted": True,
                "source_delete_error": (str(error)[:300] if error else None),
                "status": "STAGED" if deleted or error else "STAGED_DELETE_PENDING",
                "updated_at": utcnow(),
            }},
        )

    async def mark_staged_storage_failed(self, bot_id: int, item_id: str, error: str) -> None:
        await self.items.update_one(
            {"bot_id": int(bot_id), "item_id": str(item_id), "status": {"$in": ["STAGED", "STAGED_DELETE_PENDING"]}},
            {"$set": {"status": "FAILED", "error": str(error)[:400], "updated_at": utcnow()}},
        )

    async def mark_item_failed(self, bot_id: int, item: dict, error: str) -> None:
        result = await self.items.update_one(
            {"bot_id": int(bot_id), "item_id": item["item_id"], "status": {"$in": ["QUEUED", "DOWNLOADING"]}},
            {"$set": {"status": "FAILED", "error": str(error)[:400], "updated_at": utcnow()}},
        )
        if result.modified_count:
            expected = max(0, int(item.get("expected_size") or 0))
            await self.packages.update_one(
                {"bot_id": int(bot_id), "package_id": item["package_id"]},
                {"$inc": {"pending_count": -1, "item_count": -1, "reserved_bytes": -expected, "failed_count": 1}, "$set": {"updated_at": utcnow()}},
            )

    async def list_packages_to_check(self, bot_id: int, limit: int = 100) -> list[dict]:
        query = {
            "bot_id": int(bot_id),
            "$or": [
                {"state": {"$in": ["OPEN", "CLOSING", "WAITING_QUOTA", "PROCESSING", "DELIVERING", "SENT_WITH_ERRORS"]}},
                {"state": "SENT", "files_cleaned": {"$ne": True}},
            ],
        }
        return await self.packages.find(query, {"_id": 0}).sort("updated_at", 1).limit(limit).to_list(length=limit)

    async def close_package(self, bot_id: int, package_id: str, reason: str) -> None:
        await self.packages.update_one(
            {"bot_id": int(bot_id), "package_id": str(package_id), "state": "OPEN"},
            {"$set": {"state": "CLOSING", "close_reason": str(reason), "closed_at": utcnow(), "updated_at": utcnow()}},
        )

    async def expire_package_items_in(self, bot_id: int, package_id: str, expire_at) -> None:
        await self.items.update_many(
            {"bot_id": int(bot_id), "package_id": str(package_id)},
            {"$set": {"expire_at": expire_at, "updated_at": utcnow()}},
        )

    async def expire_deliveries_in(self, bot_id: int, package_id: str, expire_at) -> None:
        await self.deliveries.update_many(
            {"bot_id": int(bot_id), "package_id": str(package_id)},
            {"$set": {"expire_at": expire_at, "updated_at": utcnow()}},
        )

    async def get_package_items(self, bot_id: int, package_id: str) -> list[dict]:
        query = {"bot_id": int(bot_id), "package_id": str(package_id), "status": {"$in": ["STAGED", "STAGED_DELETE_PENDING"]}}
        return await self.items.find(query, {"_id": 0}).sort("created_at", 1).to_list(length=None)

    async def set_package_fields(self, bot_id: int, package_id: str, fields: dict, *, expected_state: str | None = None) -> bool:
        query = {"bot_id": int(bot_id), "package_id": str(package_id)}
        if expected_state:
            query["state"] = expected_state
        values = dict(fields)
        values["updated_at"] = utcnow()
        result = await self.packages.update_one(query, {"$set": values})
        return bool(result.modified_count or result.matched_count)

    async def set_status_message(self, bot_id: int, package_id: str, user_id: int, message_id: int) -> None:
        await self.packages.update_one(
            {"bot_id": int(bot_id), "package_id": str(package_id)},
            {"$set": {f"status_message_ids.{int(user_id)}": int(message_id), "updated_at": utcnow()}},
        )

    async def deliveries_for(self, bot_id: int, package_id: str) -> set[int]:
        docs = await self.deliveries.find({"bot_id": int(bot_id), "package_id": str(package_id), "status": "SENT"}, {"user_id": 1}).to_list(length=None)
        return {int(d["user_id"]) for d in docs}

    async def mark_delivery(self, bot_id: int, package_id: str, user_id: int, status: str, error: str | None = None) -> None:
        await self.deliveries.update_one(
            {"bot_id": int(bot_id), "package_id": str(package_id), "user_id": int(user_id)},
            {"$set": {"status": str(status), "error": str(error)[:300] if error else None, "updated_at": utcnow()}, "$setOnInsert": {"bot_id": int(bot_id), "package_id": str(package_id), "user_id": int(user_id), "created_at": utcnow()}},
            upsert=True,
        )

    async def get_delivery_source(self, bot_id: int, package_id: str) -> tuple[int, int] | None:
        package = await self.get_package(bot_id, package_id)
        if not package:
            return None
        chat_id = package.get("delivery_source_chat_id")
        message_id = package.get("delivery_source_message_id")
        if chat_id is None or message_id is None:
            return None
        return int(chat_id), int(message_id)

    async def list_failed_deliveries(self, bot_id: int, package_id: str) -> list[int]:
        docs = await self.deliveries.find({"bot_id": int(bot_id), "package_id": str(package_id), "status": {"$ne": "SENT"}}, {"user_id": 1}).to_list(length=None)
        return [int(d["user_id"]) for d in docs]
