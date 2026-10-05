from __future__ import annotations

import asyncio
import logging
import secrets

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputMediaPhoto, InputMediaVideo


class BroadcastService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.logger = logging.getLogger(f"broadcast.{ctx.bot_id}")
        self.semaphore = asyncio.Semaphore(ctx.settings.broadcast_concurrency_per_bot)

    async def process_single(self, job: dict) -> None:
        room_id = str(job["room_id"])
        members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
        recipients = [int(item["user_id"]) for item in members if int(item["user_id"]) != int(job["sender_id"])]
        event = await self.ctx.repositories.media.create_event(
            event_key=f"{self.ctx.bot_id}:{job['chat_id']}:{job['message_id']}",
            bot_id=self.ctx.bot_id,
            room_id=room_id,
            sender_id=int(job["sender_id"]),
            message_id=int(job["message_id"]),
            media_type=job["media_type"],
            file_id=job["file_id"],
            caption=job.get("caption"),
        )
        broadcast_id = secrets.token_hex(10)
        await self.ctx.repositories.media.create_job(self.ctx.bot_id, broadcast_id, room_id, event["event_key"], len(recipients))
        for recipient in recipients:
            ok, err = await self._deliver_one(recipient, job)
            await self.ctx.repositories.media.add_delivery(self.ctx.bot_id, broadcast_id, recipient, ok, err)
        await self.ctx.repositories.media.complete_job(self.ctx.bot_id, broadcast_id, "COMPLETED" if not recipients or await self._job_has_failures(broadcast_id) is False else "COMPLETED_WITH_ERRORS")

    async def process_album(self, job: dict) -> None:
        room_id = str(job["room_id"])
        group = job["group"]
        members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
        recipients = [int(item["user_id"]) for item in members if int(item["user_id"]) != int(group["sender_id"])]
        event_key = f"album:{self.ctx.bot_id}:{room_id}:{group['media_group_id']}"
        event = await self.ctx.repositories.media.create_event(
            event_key=event_key,
            bot_id=self.ctx.bot_id,
            room_id=room_id,
            sender_id=int(group["sender_id"]),
            message_id=int(group["items"][0]["message_id"]) if group.get("items") else 0,
            media_type="album",
            file_id="",
            caption=group.get("caption"),
        )
        broadcast_id = secrets.token_hex(10)
        await self.ctx.repositories.media.create_job(self.ctx.bot_id, broadcast_id, room_id, event["event_key"], len(recipients))
        for recipient in recipients:
            ok, err = await self._deliver_album(recipient, group)
            await self.ctx.repositories.media.add_delivery(self.ctx.bot_id, broadcast_id, recipient, ok, err)
        await self.ctx.repositories.media.complete_job(self.ctx.bot_id, broadcast_id, "COMPLETED" if not recipients or await self._job_has_failures(broadcast_id) is False else "COMPLETED_WITH_ERRORS")

    async def _job_has_failures(self, job_id: str) -> bool:
        doc = await self.ctx.repositories.media.jobs.find_one({"bot_id": self.ctx.bot_id, "job_id": job_id})
        return bool(doc and int(doc.get("failed_count", 0)) > 0)

    async def _deliver_one(self, recipient: int, job: dict) -> tuple[bool, str | None]:
        try:
            async with self.semaphore:
                await self._send_with_retry(recipient, job)
            return True, None
        except Exception as exc:
            return False, str(exc)[:500]

    async def _deliver_album(self, recipient: int, group: dict) -> tuple[bool, str | None]:
        try:
            async with self.semaphore:
                await self._send_album_with_retry(recipient, group)
            return True, None
        except Exception as exc:
            return False, str(exc)[:500]

    async def _send_with_retry(self, recipient: int, job: dict) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, self.ctx.settings.broadcast_max_retries + 1):
            try:
                if job["media_type"] == "photo":
                    await self.ctx.bot.send_photo(recipient, job["file_id"], caption=job.get("caption"), parse_mode="HTML")
                elif job["media_type"] == "video":
                    await self.ctx.bot.send_video(recipient, job["file_id"], caption=job.get("caption"), parse_mode="HTML")
                elif job["media_type"] == "animation":
                    await self.ctx.bot.send_animation(recipient, job["file_id"], caption=job.get("caption"), parse_mode="HTML")
                else:
                    await self.ctx.bot.send_document(recipient, job["file_id"], caption=job.get("caption"), parse_mode="HTML")
                return
            except TelegramRetryAfter as exc:
                last_exc = exc
                await asyncio.sleep(float(exc.retry_after) + 0.25)
            except (TelegramForbiddenError, TelegramBadRequest):
                raise
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * attempt)
        raise RuntimeError(f"envío fallido tras {self.ctx.settings.broadcast_max_retries} intentos: {last_exc}")

    async def _send_album_with_retry(self, recipient: int, group: dict) -> None:
        media = []
        for index, item in enumerate(sorted(group.get("items", []), key=lambda x: int(x.get("message_id", 0)))):
            caption = group.get("caption") if index == 0 else None
            parse_mode = "HTML" if caption else None
            if item.get("type") == "photo":
                media.append(InputMediaPhoto(media=item["file_id"], caption=caption, parse_mode=parse_mode))
            elif item.get("type") == "video":
                media.append(InputMediaVideo(media=item["file_id"], caption=caption, parse_mode=parse_mode))
        if not media:
            raise RuntimeError("El álbum no contiene fotos/vídeos compatibles")
        last_exc: Exception | None = None
        for attempt in range(1, self.ctx.settings.broadcast_max_retries + 1):
            try:
                await self.ctx.bot.send_media_group(recipient, media=media)
                return
            except TelegramRetryAfter as exc:
                last_exc = exc
                await asyncio.sleep(float(exc.retry_after) + 0.25)
            except (TelegramForbiddenError, TelegramBadRequest):
                raise
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * attempt)
        raise RuntimeError(f"álbum fallido tras {self.ctx.settings.broadcast_max_retries} intentos: {last_exc}")
