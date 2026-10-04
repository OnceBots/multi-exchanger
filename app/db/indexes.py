from __future__ import annotations

import logging


async def ensure_indexes(mongo) -> None:
    logger = logging.getLogger("mongo.indexes")
    await mongo.collection("bots").create_index("bot_id", unique=True)
    await mongo.collection("bots").create_index("status")
    await mongo.collection("users").create_index([("bot_id", 1), ("user_id", 1)], unique=True)
    await mongo.collection("rooms").create_index([("bot_id", 1), ("room_id", 1)], unique=True)
    await mongo.collection("rooms").create_index([("bot_id", 1), ("visibility", 1), ("status", 1)])
    await mongo.collection("room_members").create_index([("bot_id", 1), ("room_id", 1), ("user_id", 1)], unique=True)
    await mongo.collection("room_members").create_index([("bot_id", 1), ("user_id", 1)])
    await mongo.collection("media_events").create_index([("bot_id", 1), ("event_key", 1)], unique=True)
    await mongo.collection("media_groups").create_index([("bot_id", 1), ("room_id", 1), ("media_group_id", 1)], unique=True)
    await mongo.collection("broadcast_jobs").create_index([("bot_id", 1), ("job_id", 1)], unique=True)
    await mongo.collection("broadcast_deliveries").create_index([("bot_id", 1), ("job_id", 1), ("user_id", 1)], unique=True)
    await mongo.collection("inbound_updates").create_index([("bot_id", 1), ("update_id", 1)], unique=True)
    await mongo.collection("audit_logs").create_index([("bot_id", 1), ("created_at", -1)])
    await mongo.collection("sessions").create_index([("bot_id", 1), ("user_id", 1)], unique=True)
    await mongo.collection("admin_feeds").create_index([("bot_id", 1), ("admin_id", 1)], unique=True)
    await mongo.collection("admin_feeds").create_index([("bot_id", 1), ("enabled", 1)])
    await mongo.collection("inbound_updates").create_index("created_at", expireAfterSeconds=604800)
    await mongo.collection("broadcast_deliveries").create_index("created_at", expireAfterSeconds=2592000)
    logger.info("mongodb_indexes_ready")
