from __future__ import annotations

import logging


async def ensure_indexes(mongo) -> None:
    logger = logging.getLogger("mongo.indexes")
    bots = mongo.collection("bots")
    users = mongo.collection("users")
    rooms = mongo.collection("rooms")
    members = mongo.collection("room_members")
    media = mongo.collection("media_events")
    groups = mongo.collection("media_groups")
    jobs = mongo.collection("broadcast_jobs")
    deliveries = mongo.collection("broadcast_deliveries")
    updates = mongo.collection("inbound_updates")
    sessions = mongo.collection("sessions")
    audit = mongo.collection("audit_logs")
    feeds = mongo.collection("admin_feeds")

    await bots.create_index("bot_id", unique=True)
    await bots.create_index([("owner_id", 1), ("created_at", -1)])
    await bots.create_index("enabled")
    await bots.create_index("status")

    await users.create_index([("bot_id", 1), ("user_id", 1)], unique=True)
    await rooms.create_index([("bot_id", 1), ("room_id", 1)], unique=True)
    await rooms.create_index([("bot_id", 1), ("visibility", 1), ("status", 1)])
    await rooms.create_index([("bot_id", 1), ("invite_code", 1)], unique=True, sparse=True)
    await members.create_index([("bot_id", 1), ("room_id", 1), ("user_id", 1)], unique=True)
    await members.create_index([("bot_id", 1), ("user_id", 1)])
    await members.create_index([("bot_id", 1), ("room_id", 1), ("role", 1)])

    await media.create_index([("bot_id", 1), ("event_key", 1)], unique=True)
    await media.create_index([("bot_id", 1), ("room_id", 1), ("created_at", -1)])
    await groups.create_index([("bot_id", 1), ("room_id", 1), ("media_group_id", 1)], unique=True)
    await jobs.create_index([("bot_id", 1), ("job_id", 1)], unique=True)
    await deliveries.create_index([("bot_id", 1), ("job_id", 1), ("user_id", 1)], unique=True)
    await updates.create_index([("bot_id", 1), ("update_id", 1)], unique=True)
    await updates.create_index("created_at", expireAfterSeconds=604800)
    await sessions.create_index([("bot_id", 1), ("user_id", 1)], unique=True)
    await audit.create_index([("bot_id", 1), ("created_at", -1)])
    await feeds.create_index([("bot_id", 1), ("admin_id", 1)], unique=True)

    logger.info("mongodb_indexes_ready")
