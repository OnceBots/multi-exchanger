from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.datetime import ensure_utc
from app.core.enums import BotStatus


@dataclass(slots=True)
class BotInfo:
    bot_id: int
    username: str | None
    first_name: str | None
    owner_id: int
    status: str
    enabled: bool
    encrypted_token: str
    encrypted_webhook_secret: str
    created_at: datetime | None
    updated_at: datetime | None
    last_started_at: datetime | None
    last_heartbeat: datetime | None
    restart_count: int
    last_error: str | None
    config: dict[str, Any]

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> "BotInfo":
        return cls(
            bot_id=int(doc["bot_id"]),
            username=doc.get("username"),
            first_name=doc.get("first_name"),
            owner_id=int(doc["owner_id"]),
            status=str(doc.get("status", BotStatus.STOPPED.value)),
            enabled=bool(doc.get("enabled", True)),
            encrypted_token=str(doc.get("token_encrypted") or doc.get("encrypted_token") or ""),
            encrypted_webhook_secret=str(doc["webhook_secret_encrypted"]),
            created_at=ensure_utc(doc.get("created_at")),
            updated_at=ensure_utc(doc.get("updated_at")),
            last_started_at=ensure_utc(doc.get("last_started_at")),
            last_heartbeat=ensure_utc(doc.get("last_heartbeat")),
            restart_count=int(doc.get("restart_count", 0)),
            last_error=doc.get("last_error"),
            config=dict(doc.get("config") or {}),
        )
