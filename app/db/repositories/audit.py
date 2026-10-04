from datetime import datetime


class AuditRepository:
    def __init__(self, mongo) -> None:
        self.col = mongo.collection("audit_logs")

    async def log(self, actor_id: int, action: str, bot_id: int | None = None, metadata: dict | None = None) -> None:
        await self.col.insert_one({"actor_id": actor_id, "bot_id": bot_id, "action": action, "metadata": metadata or {}, "created_at": datetime.utcnow()})
