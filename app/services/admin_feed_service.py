from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputMediaPhoto, InputMediaVideo

from app.services.video_thumbnail_pipeline import VideoThumbnailPipeline


class AdminFeedService:
    """Send moderation media to enabled admins without publication text.

    Videos are never sent directly from the original file_id. They must first
    pass through the same mandatory thumbnail pipeline as room videos, then
    admins receive a copy of the processed message from the private buffer.
    If processing fails, delivery is blocked rather than silently sending the
    unmodified original video.
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.logger = logging.getLogger(f"admin_feed.{ctx.bot_id}")
        self.video_thumbnail_pipeline = VideoThumbnailPipeline(ctx)

    async def enabled_admins(self) -> list[int]:
        result = []
        for admin_id in self.ctx.settings.admin_ids:
            doc = await self.ctx.repositories.admin_feed.col.find_one(
                {"bot_id": self.ctx.bot_id, "admin_id": admin_id}
            )
            enabled = (
                self.ctx.settings.admin_feed_enabled_by_default
                if not doc else bool(doc.get("enabled", False))
            )
            if enabled:
                result.append(int(admin_id))
        return result

    async def process(self, item: dict) -> None:
        admins = await self.enabled_admins()
        if not admins:
            return

        media_type = item.get("media_type")
        prepared = None
        # Prepare a video only once, before any administrator receives it.
        # This is deliberately fail-closed: never fall back to the source file_id.
        if media_type == "video":
            try:
                prepared = await self.video_thumbnail_pipeline.store_video_once(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception(
                    "admin_feed_video_processing_failed_delivery_blocked bot_id=%s message_id=%s",
                    self.ctx.bot_id, item.get("message_id"),
                )
                return
        elif media_type == "album":
            group = item.get("group") or {}
            ordered_items = sorted(
                group.get("items", []), key=lambda value: int(value.get("message_id", 0))
            )
            if any(part.get("type") == "video" for part in ordered_items):
                try:
                    prepared = await self.video_thumbnail_pipeline.store_album_once(
                        {**group, "items": ordered_items, "caption": None}
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.exception(
                        "admin_feed_video_album_processing_failed_delivery_blocked bot_id=%s group_id=%s",
                        self.ctx.bot_id, group.get("media_group_id"),
                    )
                    return

        for admin_id in admins:
            try:
                await self._send_to_admin(admin_id, item, prepared)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.25)
                try:
                    await self._send_to_admin(admin_id, item, prepared)
                except TelegramForbiddenError:
                    self.logger.info("admin_feed_blocked admin_id=%s", admin_id)
                except Exception:
                    self.logger.exception(
                        "admin_feed_send_failed admin_id=%s media_type=%s", admin_id, media_type
                    )
            except TelegramForbiddenError:
                self.logger.info("admin_feed_blocked admin_id=%s", admin_id)
            except Exception:
                self.logger.exception(
                    "admin_feed_send_failed admin_id=%s media_type=%s", admin_id, media_type
                )

    async def _send_to_admin(self, admin_id: int, item: dict, prepared) -> None:
        media_type = item.get("media_type")
        if media_type == "video":
            if prepared is None:
                raise RuntimeError("Vídeo de administración bloqueado sin procesamiento de miniatura.")
            await self.ctx.bot.copy_message(
                chat_id=admin_id,
                from_chat_id=prepared.chat_id,
                message_id=int(prepared.message_id),
                protect_content=True,
            )
        elif media_type == "album":
            group = item.get("group") or {}
            if prepared is not None:
                # The stored album already contains the custom thumbnail on every
                # video. Copy those prepared messages; never rebuild from originals.
                await self.ctx.bot.copy_messages(
                    chat_id=admin_id,
                    from_chat_id=prepared.chat_id,
                    message_ids=[int(value) for value in prepared.message_ids],
                    protect_content=True,
                )
                return
            media = []
            for part in sorted(group.get("items", []), key=lambda value: int(value.get("message_id", 0))):
                if part.get("type") == "photo":
                    media.append(InputMediaPhoto(media=part["file_id"], caption=None))
                elif part.get("type") == "video":
                    # Defense in depth: a video album without a prepared buffer is
                    # not permitted to fall through to direct sending.
                    raise RuntimeError("Álbum con vídeo bloqueado sin procesamiento de miniatura.")
            if media:
                await self.ctx.bot.send_media_group(admin_id, media=media)
        elif media_type == "photo":
            await self.ctx.bot.send_photo(admin_id, item["file_id"], caption=None)
        elif media_type == "animation":
            await self.ctx.bot.send_animation(admin_id, item["file_id"], caption=None)
        elif media_type == "document":
            await self.ctx.bot.send_document(admin_id, item["file_id"], caption=None)
        else:
            self.logger.warning(
                "admin_feed_unsupported_media bot_id=%s media_type=%s", self.ctx.bot_id, media_type
            )
