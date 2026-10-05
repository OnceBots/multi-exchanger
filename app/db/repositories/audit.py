from __future__ import annotations

from app.core.datetime import utcnow


class AuditRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("audit_logs")

    async def log(self, bot_id: int, actor_id: int, action: str, target: str | None = None, details: dict | None = None) -> None:
        await self.col.insert_one({"bot_id": bot_id, "actor_id": actor_id, "action": action, "target": target, "details": details or {}, "created_at": utcnow()})
