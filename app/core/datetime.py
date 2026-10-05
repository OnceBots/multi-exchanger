from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def age_seconds(value: datetime | None, now: datetime | None = None) -> float | None:
    value = ensure_utc(value)
    if value is None:
        return None
    current = ensure_utc(now) or utcnow()
    return max(0.0, (current - value).total_seconds())
