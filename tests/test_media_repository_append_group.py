from __future__ import annotations

import asyncio

from app.db.repositories.media import MediaRepository


class _FakeCollection:
    def __init__(self, result=None):
        self.update_call = None
        self.result = result or {}

    async def update_one(self, query, update, upsert=False):
        self.update_call = {"query": query, "update": update, "upsert": upsert}

    async def find_one(self, query):
        return self.result


class _FakeMongo:
    def __init__(self, result=None):
        self.collections = {}
        self.result = result

    def collection(self, name):
        collection = self.collections.setdefault(name, _FakeCollection(self.result))
        return collection


def test_append_group_does_not_conflict_on_items_path():
    mongo = _FakeMongo()
    repo = MediaRepository(mongo)
    item = {"message_id": 12, "file_id": "file-abc", "type": "video"}

    asyncio.run(repo.append_group(1001, "room-a", "album-1", item, 2002, None))

    update = mongo.collections["media_groups"].update_call["update"]
    assert "items" not in update["$setOnInsert"]
    assert update["$push"]["items"] == item
    assert mongo.collections["media_groups"].update_call["upsert"] is True

    # MongoDB must not receive the same path from two update operators.
    operator_paths = [set(fields.keys()) for fields in update.values()]
    for index, left in enumerate(operator_paths):
        for right in operator_paths[index + 1:]:
            assert left.isdisjoint(right)


def test_append_group_still_sets_album_metadata_on_upsert():
    mongo = _FakeMongo(result={"media_group_id": "album-1"})
    repo = MediaRepository(mongo)

    result = asyncio.run(repo.append_group(1001, "room-a", "album-1", {"message_id": 12}, 2002, "caption"))

    update = mongo.collections["media_groups"].update_call["update"]
    assert update["$setOnInsert"]["bot_id"] == 1001
    assert update["$setOnInsert"]["status"] == "COLLECTING"
    assert update["$push"]["items"] == {"message_id": 12}
    assert result["media_group_id"] == "album-1"
