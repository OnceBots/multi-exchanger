from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.enums import RoomVisibility
from app.services.room_service import InsufficientReferralCredits, RoomService


class FakeRoomRepository:
    def __init__(self):
        self.created = []

    async def create(self, bot_id, owner_id, visibility, max_members, settings, duration_minutes, password=None, referral_cost=0):
        room = {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "room_id": f"room-{len(self.created) + 1}",
            "visibility": visibility,
            "referral_cost": referral_cost,
        }
        self.created.append(room)
        return room


class FakeReferralRepository:
    def __init__(self, room_repository, referral_counts):
        self.room_repository = room_repository
        self.referral_counts = dict(referral_counts)

    async def stats(self, bot_id, user_id):
        earned = self.referral_counts.get((bot_id, user_id), 0)
        paid_rooms = sum(
            1 for room in self.room_repository.created
            if room["bot_id"] == bot_id and room["owner_id"] == user_id and room["referral_cost"] >= 10
        )
        spent = paid_rooms * 10
        return {
            "valid_referrals": earned,
            "rooms_paid": paid_rooms,
            "credits_spent": spent,
            "credits_available": max(0, earned - spent),
            "credits_required_per_room": 10,
        }


def make_service(referral_counts=None, admins=()):
    rooms = FakeRoomRepository()
    referrals = FakeReferralRepository(rooms, referral_counts or {})
    return RoomService(SimpleNamespace(room=rooms, referrals=referrals), admin_ids=admins), rooms


@pytest.mark.asyncio
async def test_regular_user_without_ten_referrals_cannot_create_room():
    service, rooms = make_service(referral_counts={(1, 200): 9})
    with pytest.raises(InsufficientReferralCredits) as exc:
        await service.create_room(1, 200, RoomVisibility.PUBLIC.value, 10, {}, 0)
    assert exc.value.available == 9
    assert rooms.created == []


@pytest.mark.asyncio
async def test_regular_user_spends_ten_credits_per_room():
    service, rooms = make_service(referral_counts={(1, 200): 20})
    first = await service.create_room(1, 200, RoomVisibility.PUBLIC.value, 10, {}, 0)
    second = await service.create_room(1, 200, RoomVisibility.PUBLIC.value, 10, {}, 0)
    assert first["referral_cost"] == 10
    assert second["referral_cost"] == 10
    with pytest.raises(InsufficientReferralCredits):
        await service.create_room(1, 200, RoomVisibility.PUBLIC.value, 10, {}, 0)
    assert len(rooms.created) == 2


@pytest.mark.asyncio
async def test_admin_can_create_room_without_referral_credits():
    service, rooms = make_service(admins={999})
    room = await service.create_room(1, 999, RoomVisibility.PUBLIC.value, 10, {}, 0)
    assert room["referral_cost"] == 0
    assert len(rooms.created) == 1


@pytest.mark.asyncio
async def test_concurrent_room_creation_cannot_spend_the_same_ten_credits_twice():
    import asyncio

    service, rooms = make_service(referral_counts={(1, 200): 10})
    results = await asyncio.gather(
        service.create_room(1, 200, RoomVisibility.PUBLIC.value, 10, {}, 0),
        service.create_room(1, 200, RoomVisibility.PUBLIC.value, 10, {}, 0),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, InsufficientReferralCredits) for item in results) == 1
    assert len(rooms.created) == 1
