from __future__ import annotations

from types import SimpleNamespace

from app.services.admin_feed_service import AdminFeedService
from app.services.broadcast_service import BroadcastService
from app.services.media_service import MediaService
from app.services.room_service import RoomService


def build_child_services(ctx):
    rooms = RoomService(ctx.repositories)
    media = MediaService(ctx, rooms)
    broadcast = BroadcastService(ctx)
    admin_feed = AdminFeedService(ctx)
    ctx.services = SimpleNamespace(rooms=rooms, media=media, broadcast=broadcast, admin_feed=admin_feed)
    return ctx.services
