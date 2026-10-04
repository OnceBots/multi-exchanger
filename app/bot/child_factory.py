from __future__ import annotations

from aiogram import Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.services.admin_feed_service import AdminFeedService
from app.services.broadcast_service import RoomBroadcastService
from app.services.media_service import MediaService
from app.services.room_service import RoomService
from app.core.rate_limit import SlidingWindowLimiter


def build_child_services(ctx):
    class Repos:  # lightweight typed namespace
        user = ctx.repositories.user
        room = ctx.repositories.room
        media = ctx.repositories.media
        session = ctx.repositories.session
        audit = ctx.repositories.audit
        admin_feed = ctx.repositories.admin_feed

    class Services:
        pass

    services = Services()
    ctx.services = services
    ctx.repositories = Repos
    services.room = RoomService(Repos.room, Repos.user)
    services.media_rate = SlidingWindowLimiter(ctx.settings.max_media_per_user_per_minute)
    services.album_rate = SlidingWindowLimiter(ctx.settings.max_albums_per_user_per_minute)
    services.broadcast = RoomBroadcastService(ctx)
    services.admin_feed = AdminFeedService(ctx)
    services.media = MediaService(ctx)
    return services


def build_child_dispatcher(ctx) -> Dispatcher:
    from app.bot.child_handlers import build_router

    dp = Dispatcher()
    services = build_child_services(ctx)
    ctx.services = services
    dp.include_router(build_router(ctx))
    return dp


def new_bot(token: str):
    from aiogram import Bot
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
