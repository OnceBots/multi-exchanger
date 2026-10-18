from __future__ import annotations

from pymongo.errors import DuplicateKeyError

from app.core.datetime import utcnow


class RoomReferralRepository:
    """Unique referral attributions and derived room-creation credits."""

    ROOM_COST = 10

    def __init__(self, mongo) -> None:
        self.referrals = mongo.collection("room_referrals")
        self.users = mongo.collection("users")
        self.rooms = mongo.collection("rooms")

    async def register_referral(self, bot_id: int, referrer_id: int, referred_user_id: int) -> bool:
        bot_id, referrer_id, referred_user_id = int(bot_id), int(referrer_id), int(referred_user_id)
        if bot_id <= 0 or referrer_id <= 0 or referred_user_id <= 0 or referrer_id == referred_user_id:
            return False
        referrer = await self.users.find_one({"bot_id": bot_id, "user_id": referrer_id}, {"_id": 1})
        if not referrer:
            return False
        try:
            await self.referrals.insert_one({
                "bot_id": bot_id,
                "referrer_id": referrer_id,
                "referred_user_id": referred_user_id,
                "created_at": utcnow(),
            })
        except DuplicateKeyError:
            # One referred account can credit only one inviter in this child bot.
            return False
        return True

    async def stats(self, bot_id: int, user_id: int) -> dict[str, int]:
        bot_id, user_id = int(bot_id), int(user_id)
        earned = int(await self.referrals.count_documents({"bot_id": bot_id, "referrer_id": user_id}))
        paid_rooms = int(await self.rooms.count_documents({
            "bot_id": bot_id,
            "owner_id": user_id,
            "referral_cost": {"$gte": self.ROOM_COST},
        }))
        spent = paid_rooms * self.ROOM_COST
        return {
            "valid_referrals": earned,
            "rooms_paid": paid_rooms,
            "credits_spent": spent,
            "credits_available": max(0, earned - spent),
            "credits_required_per_room": self.ROOM_COST,
        }
