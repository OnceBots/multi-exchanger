from __future__ import annotations

import asyncio
import logging


class MediaService:
    def __init__(self, ctx, rooms) -> None:
        self.ctx = ctx
        self.rooms = rooms
        self.logger = logging.getLogger(f"media.{ctx.bot_id}")
        self.album_tasks: dict[tuple[str, int, str], asyncio.Task] = {}

    async def _global_media_policy(self) -> dict:
        """Load the per-child-bot policy and upgrade the legacy video-only default once."""
        from app.services.room_service import load_global_media_policy

        return await load_global_media_policy(self.ctx)

    def media_from_message(self, message):
        """Return the media kind and Telegram file_id for supported attachments."""
        photo = getattr(message, "photo", None)
        video = getattr(message, "video", None)
        animation = getattr(message, "animation", None)
        audio = getattr(message, "audio", None)
        voice = getattr(message, "voice", None)
        video_note = getattr(message, "video_note", None)
        sticker = getattr(message, "sticker", None)
        document = getattr(message, "document", None)
        if photo:
            return "photo", photo[-1].file_id
        if video:
            return "video", video.file_id
        if animation:
            return "animation", animation.file_id
        if audio:
            return "audio", audio.file_id
        if voice:
            return "voice", voice.file_id
        if video_note:
            return "video_note", video_note.file_id
        if sticker:
            return "sticker", sticker.file_id
        if document:
            # A user can send an MP4 as a document. Classify by MIME/extension,
            # but preserve the original filename inside the protected ZIP.
            mime_type = str(getattr(document, "mime_type", "") or "").lower()
            file_name = str(getattr(document, "file_name", "") or "").lower()
            video_extensions = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".3gp")
            if mime_type.startswith("video/") or file_name.endswith(video_extensions):
                return "video", document.file_id
            if mime_type.startswith("audio/") or file_name.endswith((".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg")):
                return "audio", document.file_id
            return "document", document.file_id
        return None, None

    @staticmethod
    def media_object_from_message(message, media_type: str):
        photo = getattr(message, "photo", None)
        if media_type == "photo" and photo:
            return photo[-1]
        attribute = {
            "video": "video", "animation": "animation", "audio": "audio",
            "voice": "voice", "video_note": "video_note", "sticker": "sticker",
        }.get(media_type)
        if attribute:
            value = getattr(message, attribute, None)
            if value is not None:
                return value
        # Files sent "as document" can still be classified as video/audio by MIME
        # type or extension; use the Document object for the authoritative size/name.
        if media_type in {"document", "video", "audio"}:
            return getattr(message, "document", None)
        return None

    async def handle_message(self, message) -> bool:
        if not message.from_user:
            return False
        user_id = int(message.from_user.id)
        session = await self.ctx.repositories.session.get(self.ctx.bot_id, user_id)
        room_id = (session or {}).get("data", {}).get("active_room_id")
        if not room_id:
            rooms = await self.ctx.repositories.room.list_for_user(self.ctx.bot_id, user_id, 50)
            usable_rooms = [room for room in rooms if room.get("status") == "ACTIVE"]
            if len(usable_rooms) == 1:
                room_id = str(usable_rooms[0]["room_id"])
            elif len(usable_rooms) > 1:
                await message.answer("🏠 Perteneces a varias salas. Abre «Mis salas», entra en la sala correcta y pulsa «Publicar multimedia» antes de enviar archivos.")
                return False
        if not room_id:
            media_type, file_id = self.media_from_message(message)
            if media_type and self.ctx.admin_feed_queue is not None and message.chat.type == "private":
                media_object = (getattr(message, "video", None) or getattr(message, "document", None)) if media_type in {"video", "audio", "document"} else None
                await self.ctx.admin_feed_queue.put({
                    "sender_id": user_id, "media_type": media_type, "file_id": file_id,
                    "message_id": int(message.message_id),
                    "file_size": int(getattr(media_object, "file_size", 0) or 0),
                    "file_name": getattr(media_object, "file_name", None),
                    "caption": None, "direct": True,
                })
                await message.answer("ℹ️ Este bot puede procesar mensajes directos para funciones de seguridad y moderación. Para compartir con una comunidad, crea o únete a una sala.")
            else:
                await message.answer("🏠 Primero crea o únete a una sala para publicar multimedia.")
            return False
        room = await self.ctx.repositories.room.get(self.ctx.bot_id, room_id)
        member = await self.ctx.repositories.room.member(self.ctx.bot_id, room_id, user_id)
        if not room or not member or room.get("status") != "ACTIVE":
            await message.answer("❌ La sala activa ya no está disponible.")
            return False

        media_type, file_id = self.media_from_message(message)
        if not media_type:
            return False
        policy = await self._global_media_policy()
        if not self.rooms.is_media_allowed(room, media_type, policy):
            await message.answer("🚫 Este formato no está habilitado en esta sala. Los administradores del bot controlan los tipos de contenido permitidos.")
            return False
        if message.media_group_id and not policy.get("allow_albums", False):
            await message.answer("🚫 Los álbumes están deshabilitados por los administradores de este bot.")
            return False

        media_object = self.media_object_from_message(message, media_type)
        expected_size = int(getattr(media_object, "file_size", 0) or 0)
        filename = getattr(media_object, "file_name", None)
        if not filename:
            # Keep extensions useful when a Telegram file object has no name.
            filename = {
                "photo": f"photo-{message.message_id}.jpg",
                "video": f"video-{message.message_id}.mp4",
                "animation": f"animation-{message.message_id}.mp4",
                "audio": f"audio-{message.message_id}.mp3",
                "voice": f"voice-{message.message_id}.ogg",
                "video_note": f"video-note-{message.message_id}.mp4",
                "sticker": f"sticker-{message.message_id}.webp",
                "document": f"document-{message.message_id}.bin",
            }.get(media_type, f"media-{message.message_id}.bin")

        accepted, item_id, reason = await self.ctx.services.packages.enqueue(
            message=message,
            room_id=str(room_id),
            media_type=media_type,
            file_id=file_id,
            expected_size=expected_size,
            filename=filename,
        )
        if not accepted:
            await message.answer(reason or "❌ No se pudo añadir el archivo al paquete. El mensaje original se conserva.")
            return False
        self.logger.info(
            "room_media_packaged_queue bot_id=%s room_id=%s message_id=%s media_type=%s expected_bytes=%s item_id=%s",
            self.ctx.bot_id, room_id, int(message.message_id), media_type, expected_size, item_id,
        )
        # The package worker deletes the incoming message only after a complete,
        # verified local copy and its durable MongoDB record both exist.
        return True

    async def _finalize_album(self, key) -> None:
        try:
            await asyncio.sleep(self.ctx.settings.album_debounce_ms / 1000)
            room_id, sender_id, group_id = key
            group = await self.ctx.repositories.media.claim_group(self.ctx.bot_id, room_id, group_id)
            if not group:
                return
            await self.ctx.album_queue.put({"room_id": room_id, "group": group})
            if group.get("items"):
                first = sorted(group["items"], key=lambda x: int(x.get("message_id", 0)))[0]
                await self.ctx.admin_feed_queue.put({
                    "sender_id": sender_id, "media_type": "album", "file_id": first.get("file_id"),
                    "message_id": int(first.get("message_id", 0)), "caption": None, "group": group,
                })
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("album_finalize_failed")
        finally:
            self.album_tasks.pop(key, None)
