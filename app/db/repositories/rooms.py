from __future__ import annotations

from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe


class RoomRepository:
    def __init__(self, mongo) -> None:
        self.rooms = mongo.collection("rooms")
        self.members = mongo.collection("room_members")

    @staticmethod
    def _expires_at(duration_minutes: int | None) -> datetime | None:
        if not duration_minutes or duration_minutes <= 0:
            return None
        return datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)

    async def create(
        self,
        bot_id: int,
        owner_id: int,
        name: str,
        description: str,
        visibility: str = "PUBLIC",
        max_members: int = 100,
        settings: dict | None = None,
        duration_minutes: int | None = None,
    ) -> dict:
        visibility = visibility.upper()
        if visibility not in {"PUBLIC", "PRIVATE"}:
            raise ValueError("visibility inválida")
        room_id = f"ROOM-{token_urlsafe(6).upper().replace('_', '-').replace('-', '')[:8]}"
        invite_code = token_urlsafe(6).upper().replace("_", "-")
        default_settings = {
            "allow_photo": True,
            "allow_video": True,
            "allow_files": True,
            "allow_albums": True,
            "moderation_mode": "STANDARD",
            "anonymous_media": True,
        }
        if settings:
            default_settings.update(settings)
        now = datetime.now(timezone.utc)
        doc = {
            "bot_id": bot_id,
            "room_id": room_id,
            "owner_id": owner_id,
            "name": name[:80].strip() or "Sala sin nombre",
            "description": description[:500].strip(),
            "type": "ROOM",
            "visibility": visibility,
            "status": "ACTIVE",
            "created_at": now,
            "updated_at": now,
            "expires_at": self._expires_at(duration_minutes),
            "max_members": max(1, int(max_members or 1)),
            "current_members": 0,
            "settings": default_settings,
            "invite_code": invite_code,
            "public_listing": visibility == "PUBLIC",
            "language": "es",
        }
        await self.rooms.insert_one(doc)
        await self.join(bot_id, room_id, owner_id, "OWNER")
        return await self.get(bot_id, room_id) or doc

    async def get(self, bot_id: int, room_id: str) -> dict | None:
        return await self.rooms.find_one({"bot_id": bot_id, "room_id": room_id.upper()})

    async def list_public(self, bot_id: int, limit: int = 20) -> list[dict]:
        return await self.rooms.find({"bot_id": bot_id, "visibility": "PUBLIC", "status": "ACTIVE", "public_listing": True}).sort("created_at", -1).limit(limit).to_list(length=limit)

    async def list_for_user(self, bot_id: int, user_id: int, limit: int = 20) -> list[dict]:
        ids = await self.members.find({"bot_id": bot_id, "user_id": user_id, "banned": False}).sort("joined_at", -1).limit(limit).to_list(length=limit)
        room_ids = [x["room_id"] for x in ids]
        if not room_ids:
            return []
        return await self.rooms.find({"bot_id": bot_id, "room_id": {"$in": room_ids}, "status": {"$ne": "DELETED"}}).sort("created_at", -1).to_list(length=limit)

    async def find_by_invite(self, bot_id: int, invite_code: str) -> dict | None:
        return await self.rooms.find_one({"bot_id": bot_id, "invite_code": invite_code.upper(), "status": "ACTIVE"})

    async def join(self, bot_id: int, room_id: str, user_id: int, role: str = "MEMBER") -> bool:
        room = await self.get(bot_id, room_id)
        if not room or room["status"] != "ACTIVE":
            return False
        existing = await self.members.find_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id})
        if existing:
            if existing.get("banned"):
                return False
            await self.members.update_one(
                {"_id": existing["_id"]},
                {"$set": {"last_seen_at": datetime.now(timezone.utc), "muted": bool(existing.get("muted", False))}},
            )
            return True
        if int(room.get("max_members", 0)) > 0 and int(room.get("current_members", 0)) >= int(room["max_members"]):
            return False
        result = await self.members.update_one(
            {"bot_id": bot_id, "room_id": room_id, "user_id": user_id},
            {
                "$setOnInsert": {
                    "bot_id": bot_id,
                    "room_id": room_id,
                    "user_id": user_id,
                    "role": role,
                    "joined_at": datetime.now(timezone.utc),
                    "last_seen_at": datetime.now(timezone.utc),
                    "muted": False,
                    "banned": False,
                    "notifications_enabled": True,
                }
            },
            upsert=True,
        )
        if result.upserted_id is not None:
            await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id}, {"$inc": {"current_members": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}})
        return True

    async def leave(self, bot_id: int, room_id: str, user_id: int) -> bool:
        result = await self.members.delete_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id, "role": {"$ne": "OWNER"}})
        if result.deleted_count:
            await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id, "current_members": {"$gt": 0}}, {"$inc": {"current_members": -1}, "$set": {"updated_at": datetime.now(timezone.utc)}})
            return True
        return False

    async def members_of(self, bot_id: int, room_id: str) -> list[dict]:
        return await self.members.find({"bot_id": bot_id, "room_id": room_id, "banned": False}).to_list(length=None)

    async def list_members(self, bot_id: int, room_id: str, limit: int = 50) -> list[dict]:
        return await self.members.find({"bot_id": bot_id, "room_id": room_id}).sort("joined_at", 1).limit(limit).to_list(length=limit)

    async def get_membership(self, bot_id: int, room_id: str, user_id: int) -> dict | None:
        return await self.members.find_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id})

    async def update_room(self, bot_id: int, room_id: str, **updates) -> dict | None:
        clean = {k: v for k, v in updates.items() if v is not None}
        if "name" in clean:
            clean["name"] = str(clean["name"])[:80].strip()
        if "description" in clean:
            clean["description"] = str(clean["description"])[:500].strip()
        clean["updated_at"] = datetime.now(timezone.utc)
        await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id}, {"$set": clean})
        return await self.get(bot_id, room_id)

    async def update_member(self, bot_id: int, room_id: str, user_id: int, **updates) -> bool:
        result = await self.members.update_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id}, {"$set": updates})
        return result.matched_count > 0

    async def close(self, bot_id: int, room_id: str) -> bool:
        result = await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id}, {"$set": {"status": "CLOSED", "public_listing": False, "updated_at": datetime.now(timezone.utc)}})
        return result.matched_count > 0

    async def pause(self, bot_id: int, room_id: str) -> bool:
        result = await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id, "status": "ACTIVE"}, {"$set": {"status": "PAUSED", "updated_at": datetime.now(timezone.utc)}})
        return result.matched_count > 0

    async def resume(self, bot_id: int, room_id: str) -> bool:
        result = await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id, "status": "PAUSED"}, {"$set": {"status": "ACTIVE", "public_listing": True, "updated_at": datetime.now(timezone.utc)}})
        return result.matched_count > 0

    async def expire_due(self) -> int:
        now = datetime.now(timezone.utc)
        result = await self.rooms.update_many({"status": {"$in": ["ACTIVE", "PAUSED"]}, "expires_at": {"$ne": None, "$lte": now}}, {"$set": {"status": "EXPIRED", "public_listing": False, "updated_at": now}})
        return int(result.modified_count)
