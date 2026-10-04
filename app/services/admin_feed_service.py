from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import InputMediaAnimation, InputMediaDocument, InputMediaPhoto, InputMediaVideo


class AdminFeedService:
    """Fan-out direct child-bot content to subscribed super-admins."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.repo = ctx.repositories.admin_feed
        self.logger = ctx.logger

    async def enqueue_message(self, message) -> bool:
        subscribers = await self.repo.subscribers(self.ctx.bot_id)
        recipients = [uid for uid in subscribers if int(uid) != int(message.from_user.id if message.from_user else 0)]
        if not recipients:
            return False
        queue = self.ctx.runtime.admin_feed_queue
        if queue is None:
            return False
        await queue.put({
            "kind": "message",
            "source_chat_id": int(message.chat.id),
            "source_message_id": int(message.message_id),
            "recipients": recipients,
            "sender_id": int(message.from_user.id) if message.from_user else None,
        })
        return True

    async def enqueue_album(self, group: dict) -> bool:
        subscribers = await self.repo.subscribers(self.ctx.bot_id)
        recipients = [uid for uid in subscribers if int(uid) != int(group.get("sender_id", 0))]
        if not recipients:
            return False
        queue = self.ctx.runtime.admin_feed_queue
        if queue is None:
            return False
        await queue.put({"kind": "album", "group": group, "recipients": recipients})
        return True

    async def process(self, job: dict) -> None:
        if job["kind"] == "message":
            await self._process_message(job)
        else:
            await self._process_album(job)

    async def _process_message(self, job: dict) -> None:
        for uid in job["recipients"]:
            await self._deliver_with_retry(
                uid,
                lambda uid=uid: self.ctx.bot.copy_message(
                    chat_id=uid,
                    from_chat_id=job["source_chat_id"],
                    message_id=job["source_message_id"],
                ),
            )
        self.ctx.runtime.metrics.inc("admin_feed_deliveries_total", amount=len(job["recipients"]))

    async def _process_album(self, job: dict) -> None:
        group = job["group"]
        items = sorted(group.get("items", []), key=lambda x: int(x["message_id"]))[:10]
        media = []
        for index, item in enumerate(items):
            caption = item.get("caption") if index == 0 else None
            kind = item["type"]
            fid = item["file_id"]
            if kind == "photo":
                media.append(InputMediaPhoto(media=fid, caption=caption))
            elif kind == "video":
                media.append(InputMediaVideo(media=fid, caption=caption))
            elif kind == "document":
                media.append(InputMediaDocument(media=fid, caption=caption))
            elif kind == "animation":
                media.append(InputMediaAnimation(media=fid, caption=caption))
        if not media:
            return
        for uid in job["recipients"]:
            await self._deliver_with_retry(uid, lambda uid=uid: self.ctx.bot.send_media_group(chat_id=uid, media=media))
        self.ctx.runtime.metrics.inc("admin_feed_deliveries_total", amount=len(job["recipients"]))
        self.ctx.runtime.metrics.inc("admin_feed_albums_total")

    async def _deliver_with_retry(self, uid: int, sender) -> None:
        try:
            await sender()
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after))
            try:
                await sender()
            except Exception as retry_exc:
                await self._record_recipient_error(uid, retry_exc)
        except (TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError, TelegramServerError) as exc:
            await self._record_recipient_error(uid, exc)
        except Exception as exc:
            self.logger.exception("admin_feed_delivery_failed bot_id=%s admin_id=%s", self.ctx.bot_id, uid)
            await self._record_recipient_error(uid, exc)

    async def _record_recipient_error(self, uid: int, exc: Exception) -> None:
        self.ctx.runtime.metrics.inc("admin_feed_failures_total")
        self.logger.warning(
            "admin_feed_recipient_error bot_id=%s admin_id=%s error=%s",
            self.ctx.bot_id,
            uid,
            str(exc)[:300],
        )
