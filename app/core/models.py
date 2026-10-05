from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from app.core.enums import BotStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class BotConfig:
    features: dict[str, bool] = field(default_factory=lambda: {"media": True, "rooms": True, "webapp": True})
    language: str = "es"
    max_members_default: int = 100

    _KNOWN_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "features",
        "language",
        "max_members_default",
    })

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "BotConfig":
        """Load config defensively so older Mongo documents cannot crash startup.

        The platform has evolved its configuration schema over time (for example,
        older documents may contain fields such as ``platform_name``). Unknown
        fields are intentionally ignored while known fields retain their values.
        """
        data = raw or {}
        filtered = {key: data[key] for key in cls._KNOWN_FIELDS if key in data}
        return cls(**filtered)


@dataclass(slots=True)
class BotInfo:
    bot_id: int
    username: str
    owner_id: int
    encrypted_token: str
    encrypted_webhook_secret: str
    enabled: bool = True
    status: BotStatus = BotStatus.CREATED
    config: BotConfig = field(default_factory=BotConfig)
    last_error: str | None = None
    restart_count: int = 0
    last_started_at: datetime | None = None
    last_heartbeat: datetime | None = None

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> "BotInfo":
        config = BotConfig.from_mapping(doc.get("config"))
        return cls(
            bot_id=int(doc["bot_id"]),
            username=doc.get("username", ""),
            owner_id=int(doc["owner_id"]),
            encrypted_token=doc["token_encrypted"],
            encrypted_webhook_secret=doc["webhook_secret_encrypted"],
            enabled=bool(doc.get("enabled", True)),
            status=BotStatus(doc.get("status", BotStatus.CREATED)),
            config=config,
            last_error=doc.get("last_error"),
            restart_count=int(doc.get("restart_count", 0)),
            last_started_at=doc.get("last_started_at"),
            last_heartbeat=doc.get("last_heartbeat"),
        )
