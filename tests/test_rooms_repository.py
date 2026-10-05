from __future__ import annotations

import pytest

from app.db.repositories.rooms import RoomRepository


class FakeCursor:
    async def to_list(self, length=None):
        return [{"room_id": "r1"}]


class FakeCollection:
    def aggregate(self, pipeline):
        async def _run():
            return FakeCursor()
        return _run()


class FakeMongo:
    def collection(self, name):
        return FakeCollection()


@pytest.mark.asyncio
async def test_list_for_user_awaits_async_aggregate():
    repo = RoomRepository(FakeMongo())
    rooms = await repo.list_for_user(123, 456, 50)
    assert rooms == [{"room_id": "r1"}]
