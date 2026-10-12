from __future__ import annotations

import asyncio
import logging
import secrets

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputMediaPhoto, InputMediaVideo

from app.services.video_thumbnail_pipeline import VideoThumbnailPipeline


class BroadcastService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.logger = logging.getLogger(f"broadcast.{ctx.bot_id}")
        self.semaphore = asyncio.Semaphore(ctx.settings.broadcast_concurrency_per_bot)
        self.video_thumbnail_pipeline = VideoThumbnailPipeline(ctx)
        self.video_thumbnail_enabled = self.video_thumbnail_pipeline.is_configured()
        if not self.video_thumbnail_enabled:
            self.logger.warning(
                "video_thumbnail_pipeline_disabled reason=configuration_invalid_or_tmpfs_unavailable"
            )

    async def process_single(self, job: dict) -> None:
        room_id = str(job["room_id"])
        members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
        recipients = [int(item["user_id"]) for item in members if int(item["user_id"]) != int(job["sender_id"])]

        delivery_job = dict(job)
        event_file_id = job["file_id"]
        use_buffer_copy = False
        if job.get("media_type") == "video" and recipients and self.video_thumbnail_enabled:
            try:
                stored = await self.video_thumbnail_pipeline.store_video_once(job)
                delivery_job["buffer_chat_id"] = stored.chat_id
                delivery_job["buffer_message_id"] = stored.message_id
                delivery_job["stored_file_id"] = stored.file_id
                event_file_id = stored.file_id
                use_buffer_copy = True
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never lose the room delivery due to a buffer/thumbnail issue.
                # This fallback keeps the original existing send_video path active.
                self.logger.exception(
                    "video_thumbnail_pipeline_failed_fallback bot_id=%s room_id=%s message_id=%s",
                    self.ctx.bot_id, room_id, job.get("message_id"),
                )

        event = await self.ctx.repositories.media.create_event(
            event_key=f"{self.ctx.bot_id}:{job['chat_id']}:{job['message_id']}",
            bot_id=self.ctx.bot_id,
            room_id=room_id,
            sender_id=int(job["sender_id"]),
            message_id=int(job["message_id"]),
            media_type=job["media_type"],
            file_id=event_file_id,
            caption=job.get("caption"),
        )
        broadcast_id = secrets.token_hex(10)
        await self.ctx.repositories.media.create_job(self.ctx.bot_id, broadcast_id, room_id, event["event_key"], len(recipients))
        for index, recipient in enumerate(recipients):
            ok, err = await self._deliver_one(recipient, delivery_job)
            await self.ctx.repositories.media.add_delivery(self.ctx.bot_id, broadcast_id, recipient, ok, err)
            if use_buffer_copy and index < len(recipients) - 1:
                delay = float(self.ctx.settings.video_delivery_delay_seconds)
                if delay > 0:
                    await asyncio.sleep(delay)
        has_failures = await self._job_has_failures(broadcast_id)
        status = "COMPLETED_WITH_ERRORS" if has_failures else "COMPLETED"
        await self.ctx.repositories.media.complete_job(self.ctx.bot_id, broadcast_id, status)

    async def process_album(self, job: dict) -> None:
        room_id = str(job["room_id"])
        group = job["group"]
        members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
        recipients = [int(item["user_id"]) for item in members if int(item["user_id"]) != int(group["sender_id"])]

        delivery_group = dict(group)
        event_file_id = ""
        use_buffer_copy = False
        has_video = any(item.get("type") == "video" for item in group.get("items", []))
        if recipients and has_video and self.video_thumbnail_enabled:
            try:
                stored = await self.video_thumbnail_pipeline.store_album_once(group)
                delivery_group["buffer_chat_id"] = stored.chat_id
                delivery_group["buffer_message_ids"] = stored.message_ids
                delivery_group["stored_video_file_ids"] = stored.video_file_ids
                event_file_id = stored.video_file_ids[0] if stored.video_file_ids else ""
                use_buffer_copy = True
            except asyncio.CancelledError:
                raise
            except Exception:
                # Preserve the existing album path if the tmpfs, thumbnail or buffer fails.
                self.logger.exception(
                    "video_album_thumbnail_pipeline_failed_fallback bot_id=%s room_id=%s group_id=%s",
                    self.ctx.bot_id, room_id, group.get("media_group_id"),
                )
                if group.get("items"):
                    event_file_id = str(group["items"][0].get("file_id") or "")
        elif group.get("items"):
            event_file_id = str(group["items"][0].get("file_id") or "")

        event_key = f"album:{self.ctx.bot_id}:{room_id}:{group['media_group_id']}"
        event = await self.ctx.repositories.media.create_event(
            event_key=event_key,
            bot_id=self.ctx.bot_id,
            room_id=room_id,
            sender_id=int(group["sender_id"]),
            message_id=int(group["items"][0]["message_id"]) if group.get("items") else 0,
            media_type="album",
            file_id=event_file_id,
            caption=group.get("caption"),
        )
        broadcast_id = secrets.token_hex(10)
        await self.ctx.repositories.media.create_job(self.ctx.bot_id, broadcast_id, room_id, event["event_key"], len(recipients))
        for index, recipient in enumerate(recipients):
            ok, err = await self._deliver_album(recipient, delivery_group)
            await self.ctx.repositories.media.add_delivery(self.ctx.bot_id, broadcast_id, recipient, ok, err)
            if use_buffer_copy and index < len(recipients) - 1:
                delay = float(self.ctx.settings.video_delivery_delay_seconds)
                if delay > 0:
                    await asyncio.sleep(delay)
        has_failures = await self._job_has_failures(broadcast_id)
        await self.ctx.repositories.media.complete_job(
            self.ctx.bot_id, broadcast_id, "COMPLETED_WITH_ERRORS" if has_failures else "COMPLETED"
        )

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
                if job.get("buffer_message_id") is not None and job.get("buffer_chat_id") is not None:
                    await self.ctx.bot.copy_message(
                        chat_id=recipient,
                        from_chat_id=job["buffer_chat_id"],
                        message_id=int(job["buffer_message_id"]),
                        protect_content=True,
                    )
                elif job["media_type"] == "photo":
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
                if group.get("buffer_message_ids") and group.get("buffer_chat_id") is not None:
                    await self.ctx.bot.copy_messages(
                        chat_id=recipient,
                        from_chat_id=group["buffer_chat_id"],
                        message_ids=[int(message_id) for message_id in group["buffer_message_ids"]],
                        protect_content=True,
                    )
                else:
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
