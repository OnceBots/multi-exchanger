from __future__ import annotations

import asyncio
import html
import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputMediaPhoto, InputMediaVideo


class AdminFeedService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.logger = logging.getLogger(f"admin_feed.{ctx.bot_id}")

    async def enabled_admins(self) -> list[int]:
        result = []
        for admin_id in self.ctx.settings.admin_ids:
            doc = await self.ctx.repositories.admin_feed.col.find_one({"bot_id": self.ctx.bot_id, "admin_id": admin_id})
            enabled = self.ctx.settings.admin_feed_enabled_by_default if not doc else bool(doc.get("enabled", False))
            if enabled:
                result.append(int(admin_id))
        return result

    async def process(self, item: dict) -> None:
        admins = await self.enabled_admins()
        if not admins:
            return
        caption = item.get("caption") or ""
        prefix = f"🛡 <b>Moderación · @{html.escape(self.ctx.bot_username or str(self.ctx.bot_id))}</b>\n👤 Usuario: <code>{item['sender_id']}</code>\n"
        text = prefix + (f"📝 {html.escape(caption[: self.ctx.settings.admin_feed_max_caption_length])}" if caption else "Sin texto adjunto.")
        for admin_id in admins:
            try:
                if item["media_type"] == "album":
                    media = []
                    group = item.get("group") or {}
                    for index, part in enumerate(sorted(group.get("items", []), key=lambda x: int(x.get("message_id", 0)))):
                        cap = text if index == 0 else None
                        if part.get("type") == "photo":
                            media.append(InputMediaPhoto(media=part["file_id"], caption=cap, parse_mode="HTML" if cap else None))
                        elif part.get("type") == "video":
                            media.append(InputMediaVideo(media=part["file_id"], caption=cap, parse_mode="HTML" if cap else None))
                    if media:
                        await self.ctx.bot.send_media_group(admin_id, media=media)
                elif item["media_type"] == "photo":
                    await self.ctx.bot.send_photo(admin_id, item["file_id"], caption=text, parse_mode="HTML")
                elif item["media_type"] == "video":
                    await self.ctx.bot.send_video(admin_id, item["file_id"], caption=text, parse_mode="HTML")
                elif item["media_type"] == "animation":
                    await self.ctx.bot.send_animation(admin_id, item["file_id"], caption=text, parse_mode="HTML")
                else:
                    await self.ctx.bot.send_document(admin_id, item["file_id"], caption=text, parse_mode="HTML")
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.25)
            except TelegramForbiddenError:
                self.logger.info("admin_feed_blocked admin_id=%s", admin_id)
            except Exception:
                self.logger.exception("admin_feed_send_failed admin_id=%s", admin_id)
