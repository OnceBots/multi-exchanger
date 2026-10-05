from datetime import datetime

from app.core.models import BotInfo


def test_bot_info_normalizes_mongo_datetime():
    info = BotInfo.from_document({
        "bot_id": 123,
        "username": "demo_bot",
        "first_name": "Demo",
        "owner_id": 10,
        "status": "RUNNING",
        "enabled": True,
        "encrypted_token": "x",
        "webhook_secret_encrypted": "y",
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 1),
        "last_started_at": datetime(2026, 1, 1),
        "last_heartbeat": datetime(2026, 1, 1),
        "restart_count": 0,
    })
    assert info.last_heartbeat.tzinfo is not None


def test_bot_info_supports_previous_token_field_name():
    info = BotInfo.from_document({
        "bot_id": 123,
        "owner_id": 10,
        "token_encrypted": "legacy-token",
        "webhook_secret_encrypted": "legacy-secret",
        "status": "RUNNING",
        "enabled": True,
    })
    assert info.encrypted_token == "legacy-token"
