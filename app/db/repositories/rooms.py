from __future__ import annotations

from datetime import datetime
from secrets import token_urlsafe


class RoomRepository:
    def __init__(self, mongo) -> None:
        self.rooms = mongo.collection("rooms")
        self.members = mongo.collection("room_members")

    async def create(self, bot_id: int, owner_id: int, name: str, description: str, visibility: str = "PUBLIC", max_members: int = 100) -> dict:
        room_id = f"ROOM-{token_urlsafe(5).upper().replace('_','-')[:8]}"
        invite_code = token_urlsafe(6).upper().replace("_", "-")
        doc = {"bot_id": bot_id, "room_id": room_id, "owner_id": owner_id, "name": name[:80], "description": description[:500], "type": "ROOM", "visibility": visibility, "status": "ACTIVE", "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(), "expires_at": None, "max_members": max(1, max_members), "current_members": 0, "settings": {"allow_photo": True, "allow_video": True, "allow_files": True, "allow_albums": True}, "invite_code": invite_code, "public_listing": visibility == "PUBLIC", "language": "es"}
        await self.rooms.insert_one(doc)
        await self.join(bot_id, room_id, owner_id, "OWNER")
        return doc

    async def get(self, bot_id: int, room_id: str) -> dict | None:
        return await self.rooms.find_one({"bot_id": bot_id, "room_id": room_id})

    async def list_public(self, bot_id: int, limit: int = 20) -> list[dict]:
        return await self.rooms.find({"bot_id": bot_id, "visibility": "PUBLIC", "status": "ACTIVE", "public_listing": True}).sort("created_at", -1).limit(limit).to_list(length=limit)

    async def list_for_user(self, bot_id: int, user_id: int, limit: int = 20) -> list[dict]:
        ids = await self.members.find({"bot_id": bot_id, "user_id": user_id}).limit(limit).to_list(length=limit)
        room_ids = [x["room_id"] for x in ids]
        if not room_ids:
            return []
        return await self.rooms.find({"bot_id": bot_id, "room_id": {"$in": room_ids}}).sort("created_at", -1).to_list(length=limit)

    async def find_by_invite(self, bot_id: int, invite_code: str) -> dict | None:
        return await self.rooms.find_one({"bot_id": bot_id, "invite_code": invite_code.upper(), "status": "ACTIVE"})

    async def join(self, bot_id: int, room_id: str, user_id: int, role: str = "MEMBER") -> bool:
        room = await self.get(bot_id, room_id)
        if not room or room["status"] != "ACTIVE":
            return False
        if int(room.get("max_members", 0)) > 0 and int(room.get("current_members", 0)) >= int(room["max_members"]):
            existing = await self.members.find_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id})
            if not existing:
                return False
        result = await self.members.update_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id}, {"$setOnInsert": {"bot_id": bot_id, "room_id": room_id, "user_id": user_id, "role": role, "joined_at": datetime.utcnow(), "last_seen_at": datetime.utcnow(), "muted": False, "banned": False, "notifications_enabled": True}}, upsert=True)
        if result.upserted_id is not None:
            await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id}, {"$inc": {"current_members": 1}, "$set": {"updated_at": datetime.utcnow()}})
            return True
        return True

    async def leave(self, bot_id: int, room_id: str, user_id: int) -> bool:
        result = await self.members.delete_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id, "role": {"$ne": "OWNER"}})
        if result.deleted_count:
            await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id, "current_members": {"$gt": 0}}, {"$inc": {"current_members": -1}, "$set": {"updated_at": datetime.utcnow()}})
            return True
        return False

    async def members_of(self, bot_id: int, room_id: str) -> list[dict]:
        return await self.members.find({"bot_id": bot_id, "room_id": room_id, "banned": False}).to_list(length=None)
