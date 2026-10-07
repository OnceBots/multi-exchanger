from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from app.core.datetime import utcnow

try:
    from pymongo import ASCENDING, DESCENDING, ReturnDocument
except Exception:  # pragma: no cover - imported in production
    ASCENDING = 1
    DESCENDING = -1
    ReturnDocument = None

from .models import CONTRIBUTION_PENDING, REPORT_DISMISSED, REPORT_OPEN, SANCTION_ACTIVE


class ContributionRepository:
    """Mongo repositories for the addon collections only."""

    def __init__(self, mongo) -> None:
        self.profiles = mongo.collection("user_economy")
        self.content = mongo.collection("contribution_items")
        self.reports = mongo.collection("content_reports")
        self.sanctions = mongo.collection("user_sanctions")
        self.settings = mongo.collection("contribution_settings")
        self.actions = mongo.collection("moderation_actions")

    async def ensure_indexes(self) -> None:
        await self.profiles.create_index([("bot_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
        await self.content.create_index([("bot_id", ASCENDING), ("contribution_id", ASCENDING)], unique=True)
        await self.content.create_index([("bot_id", ASCENDING), ("fingerprint", ASCENDING)], unique=True, sparse=True)
        await self.content.create_index([("bot_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)])
        await self.content.create_index([("bot_id", ASCENDING), ("uploader_id", ASCENDING), ("created_at", DESCENDING)])
        await self.reports.create_index([("bot_id", ASCENDING), ("report_id", ASCENDING)], unique=True)
        await self.reports.create_index([("bot_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)])
        await self.sanctions.create_index([("bot_id", ASCENDING), ("user_id", ASCENDING), ("status", ASCENDING)])
        await self.settings.create_index("bot_id", unique=True)
        await self.actions.create_index([("bot_id", ASCENDING), ("created_at", DESCENDING)])

    async def get_profile(self, bot_id: int, user_id: int) -> dict | None:
        return await self.profiles.find_one({"bot_id": bot_id, "user_id": user_id}, {"_id": 0})

    async def ensure_profile(self, bot_id: int, user_id: int, initial_credits: int) -> dict:
        now = utcnow()
        await self.profiles.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {
                "$set": {"updated_at": now},
                "$setOnInsert": {
                    "bot_id": bot_id,
                    "user_id": user_id,
                    "credits": int(initial_credits),
                    "downloads": 0,
                    "contributions": 0,
                    "approved_contributions": 0,
                    "rejected_contributions": 0,
                    "reports_submitted": 0,
                    "reputation": 0,
                    "achievements": [],
                    "created_at": now,
                },
            },
            upsert=True,
        )
        return await self.get_profile(bot_id, user_id) or {"bot_id": bot_id, "user_id": user_id, "credits": initial_credits, "reputation": 0}

    async def consume_credits(self, bot_id: int, user_id: int, cost: int) -> dict | None:
        now = utcnow()
        if cost <= 0:
            return await self.get_profile(bot_id, user_id)
        return await self.profiles.find_one_and_update(
            {"bot_id": bot_id, "user_id": user_id, "credits": {"$gte": int(cost)}},
            {"$inc": {"credits": -int(cost), "downloads": 1}, "$set": {"updated_at": now}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )

    async def grant_contribution_reward(self, bot_id: int, user_id: int, credits: int, reputation: int) -> dict | None:
        return await self.profiles.find_one_and_update(
            {"bot_id": bot_id, "user_id": user_id},
            {"$inc": {"credits": int(credits), "reputation": int(reputation), "approved_contributions": 1}, "$set": {"updated_at": utcnow()}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )

    async def apply_rejection_penalty(self, bot_id: int, user_id: int, reputation_penalty: int) -> dict | None:
        return await self.profiles.find_one_and_update(
            {"bot_id": bot_id, "user_id": user_id},
            {"$inc": {"reputation": -abs(int(reputation_penalty)), "rejected_contributions": 1}, "$set": {"updated_at": utcnow()}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )

    async def increment_contribution_counter(self, bot_id: int, user_id: int) -> None:
        await self.profiles.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$inc": {"contributions": 1}, "$set": {"updated_at": utcnow()}},
            upsert=True,
        )

    async def add_report_counter(self, bot_id: int, user_id: int) -> None:
        await self.profiles.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$inc": {"reports_submitted": 1}, "$set": {"updated_at": utcnow()}},
            upsert=True,
        )

    async def add_reputation(self, bot_id: int, user_id: int, delta: int) -> dict | None:
        return await self.profiles.find_one_and_update(
            {"bot_id": bot_id, "user_id": user_id},
            {"$inc": {"reputation": int(delta)}, "$set": {"updated_at": utcnow()}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )

    async def create_content(self, *, bot_id: int, uploader_id: int, room_id: str, media_type: str, file_id: str, fingerprint: str | None, caption: str | None, event_key: str | None = None) -> dict:
        now = utcnow()
        doc = {
            "bot_id": bot_id,
            "contribution_id": uuid4().hex,
            "uploader_id": uploader_id,
            "room_id": room_id,
            "media_type": media_type,
            "file_id": file_id,
            "fingerprint": fingerprint,
            "caption": caption,
            "event_key": event_key,
            "status": CONTRIBUTION_PENDING,
            "reviewed_by": None,
            "reviewed_at": None,
            "reward_credits": 0,
            "reward_reputation": 0,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self.content.insert_one(doc)
        except Exception as exc:
            if exc.__class__.__name__ != "DuplicateKeyError" or not fingerprint:
                raise
            existing = await self.find_duplicate(bot_id, fingerprint)
            if existing:
                return existing
            raise
        return {k: v for k, v in doc.items() if k != "_id"}

    async def get_content(self, bot_id: int, contribution_id: str) -> dict | None:
        return await self.content.find_one({"bot_id": bot_id, "contribution_id": contribution_id}, {"_id": 0})

    async def find_duplicate(self, bot_id: int, fingerprint: str | None) -> dict | None:
        if not fingerprint:
            return None
        return await self.content.find_one({"bot_id": bot_id, "fingerprint": fingerprint}, {"_id": 0})

    async def set_content_status(self, bot_id: int, contribution_id: str, status: str, reviewer_id: int, credits: int = 0, reputation: int = 0, duplicate_of: str | None = None) -> dict | None:
        return await self.content.find_one_and_update(
            {"bot_id": bot_id, "contribution_id": contribution_id, "status": CONTRIBUTION_PENDING},
            {"$set": {"status": status, "reviewed_by": reviewer_id, "reviewed_at": utcnow(), "reward_credits": int(credits), "reward_reputation": int(reputation), "duplicate_of": duplicate_of, "updated_at": utcnow()}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )

    async def list_pending_content(self, bot_id: int, limit: int = 30) -> list[dict]:
        return await self.content.find({"bot_id": bot_id, "status": CONTRIBUTION_PENDING}, {"_id": 0}).sort("created_at", DESCENDING).limit(int(limit)).to_list(length=int(limit))

    async def create_report(self, *, bot_id: int, reporter_id: int, contribution_id: str, reason: str, note: str | None = None) -> dict:
        now = utcnow()
        doc = {
            "bot_id": bot_id,
            "report_id": uuid4().hex,
            "reporter_id": reporter_id,
            "contribution_id": contribution_id,
            "reason": reason,
            "note": note,
            "status": REPORT_OPEN,
            "resolved_by": None,
            "resolution": None,
            "created_at": now,
            "resolved_at": None,
        }
        await self.reports.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    async def list_open_reports(self, bot_id: int, limit: int = 30) -> list[dict]:
        return await self.reports.find({"bot_id": bot_id, "status": REPORT_OPEN}, {"_id": 0}).sort("created_at", ASCENDING).limit(int(limit)).to_list(length=int(limit))

    async def resolve_report(self, bot_id: int, report_id: str, moderator_id: int, resolution: str, dismissed: bool = False) -> dict | None:
        status = REPORT_DISMISSED if dismissed else REPORT_RESOLVED
        return await self.reports.find_one_and_update(
            {"bot_id": bot_id, "report_id": report_id, "status": REPORT_OPEN},
            {"$set": {"status": status, "resolved_by": moderator_id, "resolution": resolution, "resolved_at": utcnow()}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )

    async def active_sanction(self, bot_id: int, user_id: int) -> dict | None:
        now = utcnow()
        await self.sanctions.update_many(
            {"bot_id": bot_id, "user_id": user_id, "status": SANCTION_ACTIVE, "expires_at": {"$lte": now}},
            {"$set": {"status": "EXPIRED"}},
        )
        return await self.sanctions.find_one({"bot_id": bot_id, "user_id": user_id, "status": SANCTION_ACTIVE}, {"_id": 0})

    async def create_sanction(self, *, bot_id: int, user_id: int, moderator_id: int, kind: str, reason: str, minutes: int | None = None) -> dict:
        now = utcnow()
        expires = now + timedelta(minutes=int(minutes)) if minutes and minutes > 0 else None
        doc = {"bot_id": bot_id, "sanction_id": uuid4().hex, "user_id": user_id, "moderator_id": moderator_id, "kind": kind, "reason": reason, "status": SANCTION_ACTIVE, "created_at": now, "expires_at": expires}
        await self.sanctions.update_many({"bot_id": bot_id, "user_id": user_id, "status": SANCTION_ACTIVE}, {"$set": {"status": "REVOKED"}})
        await self.sanctions.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    async def log_action(self, *, bot_id: int, moderator_id: int, action: str, target_id: str | None = None, details: dict | None = None) -> None:
        await self.actions.insert_one({"bot_id": bot_id, "moderator_id": moderator_id, "action": action, "target_id": target_id, "details": details or {}, "created_at": utcnow()})

    async def leaderboard(self, bot_id: int, limit: int = 10) -> list[dict]:
        return await self.profiles.find({"bot_id": bot_id}, {"_id": 0}).sort([("reputation", DESCENDING), ("approved_contributions", DESCENDING), ("credits", DESCENDING)]).limit(int(limit)).to_list(length=int(limit))

    async def stats(self, bot_id: int) -> dict:
        pending = await self.content.count_documents({"bot_id": bot_id, "status": CONTRIBUTION_PENDING})
        open_reports = await self.reports.count_documents({"bot_id": bot_id, "status": REPORT_OPEN})
        users = await self.profiles.count_documents({"bot_id": bot_id})
        approved = await self.content.count_documents({"bot_id": bot_id, "status": CONTRIBUTION_APPROVED})
        rejected = await self.content.count_documents({"bot_id": bot_id, "status": CONTRIBUTION_REJECTED})
        return {"users": int(users), "pending_contributions": int(pending), "open_reports": int(open_reports), "approved_contributions": int(approved), "rejected_contributions": int(rejected)}

    async def get_settings(self, bot_id: int) -> dict | None:
        return await self.settings.find_one({"bot_id": bot_id}, {"_id": 0})

    async def upsert_settings(self, bot_id: int, values: dict) -> dict:
        allowed = {
            "initial_credits", "approved_contribution_credits", "approved_contribution_reputation", "download_cost",
            "rejected_reputation_penalty", "duplicate_reputation_penalty", "report_reputation_bonus", "minimum_ratio", "new_user_downloads",
        }
        clean = {k: v for k, v in values.items() if k in allowed}
        clean["bot_id"] = bot_id
        clean["updated_at"] = utcnow()
        await self.settings.update_one({"bot_id": bot_id}, {"$set": clean, "$setOnInsert": {"created_at": utcnow()}}, upsert=True)
        return await self.get_settings(bot_id) or clean
