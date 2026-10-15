from __future__ import annotations

import asyncio
import logging
import secrets

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputMediaPhoto

from app.services.video_thumbnail_pipeline import VideoThumbnailPipeline


class BroadcastService:
    """Fan media out to room members with mandatory thumbnail processing for videos.

    Room deliveries are media-only: captions and text are never copied or sent.
    Videos (including videos in albums) fail closed unless the thumbnail pipeline
    has uploaded them to the storage channel successfully.
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.logger = logging.getLogger(f"broadcast.{ctx.bot_id}")
        self.semaphore = asyncio.Semaphore(ctx.settings.broadcast_concurrency_per_bot)
        self.video_thumbnail_pipeline = VideoThumbnailPipeline(ctx)
        self.video_thumbnail_enabled = self.video_thumbnail_pipeline.is_configured()
        if not self.video_thumbnail_enabled:
            self.logger.warning(
                "video_thumbnail_pipeline_unavailable videos_will_be_rejected bot_id=%s",
                ctx.bot_id,
            )

    async def _record_rejected_media(
        self,
        *,
        room_id: str,
        event_key: str,
        sender_id: int,
        message_id: int,
        media_type: str,
        source_file_id: str,
        recipients: list[int],
        reason: str,
    ) -> None:
        """Record a blocked delivery without sending the media to any recipient."""
        event = await self.ctx.repositories.media.create_event(
            event_key=event_key,
            bot_id=self.ctx.bot_id,
            room_id=room_id,
            sender_id=sender_id,
            message_id=message_id,
            media_type=media_type,
            file_id=source_file_id,
            caption=None,
        )
        broadcast_id = secrets.token_hex(10)
        await self.ctx.repositories.media.create_job(
            self.ctx.bot_id, broadcast_id, room_id, event["event_key"], len(recipients)
        )
        for recipient in recipients:
            await self.ctx.repositories.media.add_delivery(
                self.ctx.bot_id, broadcast_id, recipient, False, reason[:500]
            )
        await self.ctx.repositories.media.complete_job(self.ctx.bot_id, broadcast_id, "FAILED")
        self.logger.error(
            "room_media_rejected bot_id=%s room_id=%s media_type=%s recipients=%s reason=%s",
            self.ctx.bot_id, room_id, media_type, len(recipients), reason,
        )

    async def process_single(self, job: dict) -> None:
        room_id = str(job["room_id"])
        sender_id = int(job["sender_id"])
        message_id = int(job["message_id"])
        media_type = str(job["media_type"])
        members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
        recipients = [int(item["user_id"]) for item in members if int(item["user_id"]) != sender_id]

        # Captions stay out of room broadcasts and out of the video buffer.
        delivery_job = {**job, "caption": None}
        event_file_id = str(job["file_id"])
        use_buffer_copy = False
        event_key = f"{self.ctx.bot_id}:{job['chat_id']}:{message_id}"

        if media_type == "video" and recipients:
            if not self.video_thumbnail_enabled:
                await self._record_rejected_media(
                    room_id=room_id, event_key=event_key, sender_id=sender_id,
                    message_id=message_id, media_type=media_type,
                    source_file_id=event_file_id, recipients=recipients,
                    reason="Vídeo bloqueado: la miniatura predeterminada, tmpfs o canal buffer no está configurado correctamente.",
                )
                return
            try:
                stored = await self.video_thumbnail_pipeline.store_video_once(delivery_job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._record_rejected_media(
                    room_id=room_id, event_key=event_key, sender_id=sender_id,
                    message_id=message_id, media_type=media_type,
                    source_file_id=event_file_id, recipients=recipients,
                    reason=f"Vídeo bloqueado porque falló el proceso obligatorio de miniatura: {type(exc).__name__}.",
                )
                self.logger.exception(
                    "video_thumbnail_pipeline_failed_delivery_blocked bot_id=%s room_id=%s message_id=%s",
                    self.ctx.bot_id, room_id, message_id,
                )
                return
            delivery_job.update(
                buffer_chat_id=stored.chat_id,
                buffer_message_id=stored.message_id,
                stored_file_id=stored.file_id,
            )
            event_file_id = stored.file_id
            use_buffer_copy = True

        event = await self.ctx.repositories.media.create_event(
            event_key=event_key,
            bot_id=self.ctx.bot_id,
            room_id=room_id,
            sender_id=sender_id,
            message_id=message_id,
            media_type=media_type,
            file_id=event_file_id,
            caption=None,
        )
        broadcast_id = secrets.token_hex(10)
        await self.ctx.repositories.media.create_job(
            self.ctx.bot_id, broadcast_id, room_id, event["event_key"], len(recipients)
        )
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
        sender_id = int(group["sender_id"])
        items = sorted(group.get("items", []), key=lambda item: int(item.get("message_id", 0)))
        first_message_id = int(items[0]["message_id"]) if items else 0
        members = await self.ctx.repositories.room.members_for_room(self.ctx.bot_id, room_id)
        recipients = [int(item["user_id"]) for item in members if int(item["user_id"]) != sender_id]

        # Ignore any inbound caption so neither native albums nor buffered copies
        # can attach text to a room post.
        delivery_group = {**group, "items": items, "caption": None}
        event_file_id = str(items[0].get("file_id") or "") if items else ""
        use_buffer_copy = False
        has_video = any(item.get("type") == "video" for item in items)
        event_key = f"album:{self.ctx.bot_id}:{room_id}:{group['media_group_id']}"

        if recipients and has_video:
            if not self.video_thumbnail_enabled:
                await self._record_rejected_media(
                    room_id=room_id, event_key=event_key, sender_id=sender_id,
                    message_id=first_message_id, media_type="album",
                    source_file_id=event_file_id, recipients=recipients,
                    reason="Álbum bloqueado: todos los vídeos requieren la miniatura predeterminada y el canal buffer.",
                )
                return
            try:
                stored = await self.video_thumbnail_pipeline.store_album_once(delivery_group)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._record_rejected_media(
                    room_id=room_id, event_key=event_key, sender_id=sender_id,
                    message_id=first_message_id, media_type="album",
                    source_file_id=event_file_id, recipients=recipients,
                    reason=f"Álbum bloqueado porque falló el proceso obligatorio de miniatura: {type(exc).__name__}.",
                )
                self.logger.exception(
                    "video_album_thumbnail_pipeline_failed_delivery_blocked bot_id=%s room_id=%s group_id=%s",
                    self.ctx.bot_id, room_id, group.get("media_group_id"),
                )
                return
            delivery_group.update(
                buffer_chat_id=stored.chat_id,
                buffer_message_ids=stored.message_ids,
                stored_video_file_ids=stored.video_file_ids,
            )
            event_file_id = stored.video_file_ids[0] if stored.video_file_ids else event_file_id
            use_buffer_copy = True

        event = await self.ctx.repositories.media.create_event(
            event_key=event_key,
            bot_id=self.ctx.bot_id,
            room_id=room_id,
            sender_id=sender_id,
            message_id=first_message_id,
            media_type="album",
            file_id=event_file_id,
            caption=None,
        )
        broadcast_id = secrets.token_hex(10)
        await self.ctx.repositories.media.create_job(
            self.ctx.bot_id, broadcast_id, room_id, event["event_key"], len(recipients)
        )
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
                media_type = job["media_type"]
                if job.get("buffer_message_id") is not None and job.get("buffer_chat_id") is not None:
                    await self.ctx.bot.copy_message(
                        chat_id=recipient,
                        from_chat_id=job["buffer_chat_id"],
                        message_id=int(job["buffer_message_id"]),
                        protect_content=True,
                    )
                elif media_type == "video":
                    # Fail closed. Direct video sends bypass the required thumbnail.
                    raise RuntimeError("Vídeo bloqueado: falta la copia procesada con la miniatura predeterminada.")
                elif media_type == "photo":
                    await self.ctx.bot.send_photo(recipient, job["file_id"], caption=None)
                elif media_type == "animation":
                    await self.ctx.bot.send_animation(recipient, job["file_id"], caption=None)
                elif media_type == "document":
                    await self.ctx.bot.send_document(recipient, job["file_id"], caption=None)
                else:
                    raise RuntimeError(f"Tipo multimedia no permitido para sala: {media_type}")
                return
            except TelegramRetryAfter as exc:
                last_exc = exc
                await asyncio.sleep(float(exc.retry_after) + 0.25)
            except (TelegramForbiddenError, TelegramBadRequest):
                raise
            except RuntimeError:
                raise
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * attempt)
        raise RuntimeError(f"envío fallido tras {self.ctx.settings.broadcast_max_retries} intentos: {last_exc}")

    async def _send_album_with_retry(self, recipient: int, group: dict) -> None:
        items = sorted(group.get("items", []), key=lambda x: int(x.get("message_id", 0)))
        has_video = any(item.get("type") == "video" for item in items)
        has_buffer = bool(group.get("buffer_message_ids")) and group.get("buffer_chat_id") is not None
        if has_video and not has_buffer:
            raise RuntimeError("Álbum bloqueado: todo vídeo debe pasar por la miniatura predeterminada.")

        media = []
        if not has_buffer:
            # The guard above guarantees this path is photo-only.
            for item in items:
                if item.get("type") == "photo":
                    media.append(InputMediaPhoto(media=item["file_id"], caption=None))
        if not has_buffer and not media:
            raise RuntimeError("El álbum no contiene fotos compatibles")
        last_exc: Exception | None = None
        for attempt in range(1, self.ctx.settings.broadcast_max_retries + 1):
            try:
                if has_buffer:
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
