from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def tenant_filter(bot_id: int, **filters: Any) -> dict[str, Any]:
    return {"bot_id": bot_id, **filters}
