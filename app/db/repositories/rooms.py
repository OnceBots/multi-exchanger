from __future__ import annotations

import secrets
import string

from app.core.datetime import utcnow
from app.core.enums import MemberRole, RoomStatus, RoomVisibility


class RoomRepository:
    def __init__(self, mongo) -> None:
        self.rooms = mongo.collection("rooms")
        self.members = mongo.collection("room_members")

    def _code(self) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(8))

    async def create(self, bot_id: int, owner_id: int, name: str, description: str, visibility: str, max_members: int, settings: dict, duration_minutes: int) -> dict:
        created = utcnow()
        expires_at = None
        if duration_minutes > 0:
            from datetime import timedelta
            expires_at = created + timedelta(minutes=duration_minutes)
        room_id = secrets.token_hex(8)
        invite_code = self._code()
        room = {
            "bot_id": bot_id,
            "room_id": room_id,
            "invite_code": invite_code,
            "owner_id": owner_id,
            "name": name,
            "description": description,
            "visibility": visibility,
            "max_members": max_members,
            "settings": settings,
            "status": RoomStatus.ACTIVE.value,
            "member_count": 1,
            "created_at": created,
            "expires_at": expires_at,
            "updated_at": created,
        }
        await self.rooms.insert_one(room)
        await self.members.insert_one({"bot_id": bot_id, "room_id": room_id, "user_id": owner_id, "role": MemberRole.OWNER.value, "joined_at": created})
        return room

    async def get(self, bot_id: int, room_id: str) -> dict | None:
        return await self.rooms.find_one({"bot_id": bot_id, "room_id": room_id})

    async def resolve(self, bot_id: int, ref: str) -> dict | None:
        ref = ref.strip()
        room = await self.rooms.find_one({"bot_id": bot_id, "room_id": ref})
        if room:
            return room
        return await self.rooms.find_one({"bot_id": bot_id, "invite_code": ref.upper()})

    async def list_public(self, bot_id: int, limit: int = 20) -> list[dict]:
        return await self.rooms.find({"bot_id": bot_id, "visibility": RoomVisibility.PUBLIC.value, "status": RoomStatus.ACTIVE.value}).sort("created_at", -1).limit(limit).to_list(length=limit)

    async def list_for_user(self, bot_id: int, user_id: int, limit: int = 50) -> list[dict]:
        pipeline = [
            {"$match": {"bot_id": bot_id, "user_id": user_id}},
            {"$lookup": {"from": "rooms", "let": {"rid": "$room_id", "bid": "$bot_id"}, "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$room_id", "$$rid"]}, {"$eq": ["$bot_id", "$$bid"]}]}}}], "as": "room"}},
            {"$unwind": "$room"},
            {"$replaceRoot": {"newRoot": "$room"}},
            {"$sort": {"created_at": -1}},
            {"$limit": limit},
        ]
        cursor = await self.members.aggregate(pipeline)
        return await cursor.to_list(length=limit)

    async def is_member(self, bot_id: int, room_id: str, user_id: int) -> bool:
        return bool(await self.members.find_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id}, {"_id": 1}))

    async def member(self, bot_id: int, room_id: str, user_id: int) -> dict | None:
        return await self.members.find_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id})

    async def members_for_room(self, bot_id: int, room_id: str) -> list[dict]:
        return await self.members.find({"bot_id": bot_id, "room_id": room_id}).sort("joined_at", 1).to_list(length=None)

    async def join(self, bot_id: int, room_id: str, user_id: int) -> tuple[bool, str]:
        room = await self.get(bot_id, room_id)
        if not room or room.get("status") != RoomStatus.ACTIVE.value:
            return False, "La sala no existe o está inactiva."
        existing = await self.is_member(bot_id, room_id, user_id)
        if existing:
            return True, "Ya eres miembro."
        if int(room.get("member_count", 0)) >= int(room.get("max_members", 999999)):
            return False, "La sala está llena."
        now = utcnow()
        try:
            await self.members.insert_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id, "role": MemberRole.MEMBER.value, "joined_at": now})
        except Exception as exc:
            if exc.__class__.__name__ == "DuplicateKeyError":
                return True, "Ya eres miembro."
            raise
        await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id}, {"$inc": {"member_count": 1}, "$set": {"updated_at": now}})
        return True, "Te uniste correctamente."

    async def leave(self, bot_id: int, room_id: str, user_id: int) -> tuple[bool, str]:
        room = await self.get(bot_id, room_id)
        member = await self.member(bot_id, room_id, user_id)
        if not room or not member:
            return False, "No perteneces a esa sala."
        if member.get("role") == MemberRole.OWNER.value:
            others = await self.members_for_room(bot_id, room_id)
            others = [item for item in others if int(item["user_id"]) != user_id]
            if others:
                new_owner = others[0]
                await self.members.update_one({"_id": new_owner["_id"]}, {"$set": {"role": MemberRole.OWNER.value}})
                await self.members.update_one({"_id": member["_id"]}, {"$set": {"role": MemberRole.MEMBER.value}})
        await self.members.delete_one({"_id": member["_id"]})
        await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id}, {"$inc": {"member_count": -1}, "$set": {"updated_at": utcnow()}})
        return True, "Has salido de la sala."

    async def set_role(self, bot_id: int, room_id: str, user_id: int, role: str) -> None:
        await self.members.update_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id}, {"$set": {"role": role}})

    async def remove_member(self, bot_id: int, room_id: str, user_id: int) -> None:
        result = await self.members.delete_one({"bot_id": bot_id, "room_id": room_id, "user_id": user_id})
        if result.deleted_count:
            await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id}, {"$inc": {"member_count": -1}, "$set": {"updated_at": utcnow()}})

    async def update(self, bot_id: int, room_id: str, **fields) -> None:
        if "expires_at" in fields or "name" in fields or "description" in fields:
            pass
        fields["updated_at"] = utcnow()
        await self.rooms.update_one({"bot_id": bot_id, "room_id": room_id}, {"$set": fields})

    async def expire_due(self) -> int:
        now = utcnow()
        result = await self.rooms.update_many({"status": RoomStatus.ACTIVE.value, "expires_at": {"$ne": None, "$lte": now}}, {"$set": {"status": RoomStatus.EXPIRED.value, "updated_at": now}})
        return int(result.modified_count)
