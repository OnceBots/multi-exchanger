from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import InputMediaAnimation, InputMediaDocument, InputMediaPhoto, InputMediaVideo


class RoomBroadcastService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.logger = ctx.logger
        self.room_repo = ctx.repositories.room
        self.media_repo = ctx.repositories.media

    async def enqueue_single(self, room_id: str, sender_id: int, source_chat_id: int, source_message_id: int, event_id: str, message) -> None:
        if self.ctx.runtime.broadcast_queue is None:
            raise RuntimeError("broadcast_queue no inicializada")
        job_id = f"single:{event_id}"
        await self.media_repo.create_job(job_id, self.ctx.bot_id, room_id, event_id, 0)
        await self.ctx.runtime.broadcast_queue.put({
            "kind": "single",
            "job_id": job_id,
            "room_id": room_id,
            "sender_id": sender_id,
            "source_chat_id": source_chat_id,
            "source_message_id": source_message_id,
        })

    async def enqueue_album(self, group: dict) -> None:
        if self.ctx.runtime.album_queue is None:
            raise RuntimeError("album_queue no inicializada")
        job_id = f"album:{group['media_group_id']}"
        await self.media_repo.create_job(job_id, self.ctx.bot_id, group["room_id"], job_id, 0)
        await self.ctx.runtime.album_queue.put({"kind": "album", "job_id": job_id, "group": group})

    async def process_single(self, job: dict) -> None:
        members = await self.room_repo.members_of(self.ctx.bot_id, job["room_id"])
        recipients = [m for m in members if int(m["user_id"]) != int(job["sender_id"])]
        await self.ctx.db["broadcast_jobs"].update_one({"bot_id": self.ctx.bot_id, "job_id": job["job_id"]}, {"$set": {"recipients_count": len(recipients)}})

        async def deliver(uid: int) -> None:
            try:
                await self.ctx.bot.copy_message(chat_id=uid, from_chat_id=job["source_chat_id"], message_id=job["source_message_id"])
                await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, True)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                try:
                    await self.ctx.bot.copy_message(chat_id=uid, from_chat_id=job["source_chat_id"], message_id=job["source_message_id"])
                    await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, True)
                except Exception as retry_exc:
                    await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, False, str(retry_exc)[:500])
            except (TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError, TelegramServerError) as exc:
                await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, False, str(exc)[:500])
            except Exception as exc:
                self.logger.exception("broadcast_recipient_error bot_id=%s user_id=%s", self.ctx.bot_id, uid)
                await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, False, str(exc)[:500])

        await self._bounded_fanout([int(x["user_id"]) for x in recipients], deliver)
        await self.media_repo.complete_job(self.ctx.bot_id, job["job_id"])
        self.ctx.runtime.metrics.inc("broadcast_jobs_total")

    async def process_album(self, job: dict) -> None:
        group = job["group"]
        members = await self.room_repo.members_of(self.ctx.bot_id, group["room_id"])
        recipients = [m for m in members if int(m["user_id"]) != int(group["sender_id"])]
        await self.ctx.db["broadcast_jobs"].update_one({"bot_id": self.ctx.bot_id, "job_id": job["job_id"]}, {"$set": {"recipients_count": len(recipients)}})

        items = sorted(group["items"], key=lambda x: int(x["message_id"]))[:10]
        media: list = []
        for item in items:
            caption = item.get("caption") if int(item["message_id"]) == int(items[0]["message_id"]) else None
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

        async def deliver(uid: int) -> None:
            try:
                await self.ctx.bot.send_media_group(chat_id=uid, media=media)
                await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, True)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                try:
                    await self.ctx.bot.send_media_group(chat_id=uid, media=media)
                    await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, True)
                except Exception as retry_exc:
                    await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, False, str(retry_exc)[:500])
            except (TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError, TelegramServerError) as exc:
                await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, False, str(exc)[:500])
            except Exception as exc:
                self.logger.exception("album_recipient_error bot_id=%s user_id=%s", self.ctx.bot_id, uid)
                await self.media_repo.add_delivery(self.ctx.bot_id, job["job_id"], uid, False, str(exc)[:500])

        await self._bounded_fanout([int(x["user_id"]) for x in recipients], deliver)
        await self.media_repo.complete_job(self.ctx.bot_id, job["job_id"])
        self.ctx.runtime.metrics.inc("albums_processed_total")

    async def _bounded_fanout(self, user_ids: list[int], handler) -> None:
        queue: asyncio.Queue[int] = asyncio.Queue()
        for uid in user_ids:
            queue.put_nowait(uid)

        workers = min(max(1, self.ctx.settings.broadcast_max_concurrency), len(user_ids))
        if not workers:
            return

        async def worker() -> None:
            while True:
                try:
                    uid = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await handler(uid)
                finally:
                    queue.task_done()

        tasks = [asyncio.create_task(worker()) for _ in range(workers)]
        await asyncio.gather(*tasks, return_exceptions=True)
