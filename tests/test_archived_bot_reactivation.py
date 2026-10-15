from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.models import BotInfo
from app.services.child_bot_provisioner import ChildBotProvisioner


class FakeBotRepository:
    def __init__(self, document=None):
        self.document = document
        self.created = []

    async def get(self, bot_id):
        if self.document and int(self.document["bot_id"]) == int(bot_id):
            return self.document
        return None

    async def create(self, document):
        self.created.append(document)
        self.document = dict(document)

    async def reactivate_archived(self, bot_id, updates):
        doc = await self.get(bot_id)
        lifecycle = (doc or {}).get("config", {}).get("lifecycle", {})
        if not doc or not (
            str(doc.get("status", "")).upper() == "ARCHIVED"
            or str(lifecycle.get("mode", "")).upper() == "ARCHIVED"
        ):
            return False
        doc.update(updates)
        doc["enabled"] = True
        doc["status"] = "STARTING"
        doc.pop("archived_at", None)
        for key in ("mode", "reason", "archived_at", "delete_at", "scheduled_at", "scheduled_by", "label"):
            lifecycle.pop(key, None)
        return True


class FakeTokenService:
    async def validate_token(self, token):
        assert token == "123456:valid-token"
        return 123456, "recovered_bot", "Recovered"

    def new_webhook_secret(self):
        return "fresh-webhook-secret"

    def encrypt(self, value):
        return f"encrypted:{value}"


class FakeManager:
    def __init__(self, document=None, admin_ids=()):
        self.repositories = SimpleNamespace(bots=FakeBotRepository(document))
        self.token_service = FakeTokenService()
        self.settings = SimpleNamespace(admin_ids=frozenset(admin_ids))
        self.registry = {}
        self.started = []
        self.admin_notices = []
        self.creator_notices = []

    async def start_bot(self, bot_id):
        self.started.append(bot_id)
        self.repositories.bots.document["status"] = "RUNNING"

    async def stop_bot(self, bot_id):
        self.registry.pop(bot_id, None)
        self.repositories.bots.document["status"] = "STOPPED"

    async def get_info(self, bot_id):
        return BotInfo.from_document(self.repositories.bots.document)

    async def notify_admins_new_bot(self, info, creator_id, reactivated=False):
        self.admin_notices.append((info.bot_id, creator_id, reactivated))

    async def notify_creator_new_bot(self, info, creator_id):
        self.creator_notices.append((info.bot_id, creator_id))

    class DummyBots:
        pass


def archived_document():
    return {
        "bot_id": 123456,
        "username": "old_username",
        "first_name": "Old",
        "owner_id": 10,
        "status": "ARCHIVED",
        "enabled": False,
        "token_encrypted": "encrypted:old-token",
        "webhook_secret_encrypted": "encrypted:old-secret",
        "created_at": None,
        "updated_at": None,
        "last_started_at": None,
        "last_heartbeat": None,
        "restart_count": 4,
        "last_error": "old error",
        "archived_at": "old-date",
        "config": {
            "created_from": "master_chat",
            "room_preferences": {"allow_video": True, "retention": 7},
            "lifecycle": {
                "mode": "ARCHIVED",
                "reason": "manual",
                "archived_at": "old-date",
                "delete_at": "old-delete-date",
            },
        },
    }


@pytest.mark.asyncio
async def test_archived_bot_is_reactivated_preserving_owner_and_config():
    original = archived_document()
    manager = FakeManager(original)
    provisioner = ChildBotProvisioner(manager)

    info = await provisioner.create("  123456:valid-token  ", owner_id=10, metadata={"should_not_replace_config": True})

    doc = manager.repositories.bots.document
    assert manager.repositories.bots.created == []
    assert manager.started == [123456]
    assert doc["owner_id"] == 10
    assert doc["created_at"] is None
    assert doc["restart_count"] == 4
    assert doc["username"] == "recovered_bot"
    assert doc["token_encrypted"] == "encrypted:123456:valid-token"
    assert doc["webhook_secret_encrypted"] == "encrypted:fresh-webhook-secret"
    assert doc["enabled"] is True
    assert doc["status"] == "RUNNING"
    assert doc["config"]["room_preferences"] == {"allow_video": True, "retention": 7}
    assert doc["config"]["created_from"] == "master_chat"
    assert "should_not_replace_config" not in doc["config"]
    assert "mode" not in doc["config"]["lifecycle"]
    assert "archived_at" not in doc
    assert info.config["_reactivated_in_this_request"] is True
    assert manager.admin_notices == [(123456, 10, True)]
    assert manager.creator_notices == [(123456, 10)]


@pytest.mark.asyncio
async def test_non_owner_cannot_reactivate_archived_bot_unless_admin():
    manager = FakeManager(archived_document())
    provisioner = ChildBotProvisioner(manager)

    with pytest.raises(ValueError, match="propietario original"):
        await provisioner.create("123456:valid-token", owner_id=99)
    assert manager.started == []
    assert manager.repositories.bots.document["status"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_active_duplicate_is_still_rejected():
    document = archived_document()
    document.update(status="RUNNING", enabled=True)
    manager = FakeManager(document)
    provisioner = ChildBotProvisioner(manager)

    with pytest.raises(ValueError, match="ya está registrado"):
        await provisioner.create("123456:valid-token", owner_id=10)
    assert manager.started == []
    assert manager.repositories.bots.created == []


@pytest.mark.asyncio
async def test_repository_reactivation_is_atomic_and_only_clears_lifecycle_markers():
    from app.db.repositories.bots import BotRepository

    class Result:
        matched_count = 1

    class Collection:
        def __init__(self):
            self.query = None
            self.update = None

        async def update_one(self, query, update):
            self.query = query
            self.update = update
            return Result()

    class Mongo:
        def __init__(self):
            self.col = Collection()

        def collection(self, name):
            assert name == "bots"
            return self.col

    mongo = Mongo()
    repository = BotRepository(mongo)
    result = await repository.reactivate_archived(
        123456,
        {"token_encrypted": "new-encrypted-token", "webhook_secret_encrypted": "new-secret"},
    )

    assert result is True
    assert mongo.col.query["bot_id"] == 123456
    assert mongo.col.query["$or"] == [
        {"status": "ARCHIVED"},
        {"config.lifecycle.mode": "ARCHIVED"},
    ]
    assert mongo.col.update["$set"]["enabled"] is True
    assert mongo.col.update["$set"]["status"] == "STARTING"
    assert mongo.col.update["$set"]["token_encrypted"] == "new-encrypted-token"
    assert "owner_id" not in mongo.col.update["$set"]
    assert "config" not in mongo.col.update["$set"]
    assert "config.lifecycle.mode" in mongo.col.update["$unset"]
