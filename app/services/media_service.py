from __future__ import annotations

import asyncio
import secrets


ADMIN_FEED_ROOM_KEY = "__ADMIN_FEED__"


class MediaService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.repo = ctx.repositories.media
        self.room_repo = ctx.repositories.room
        self.broadcast = ctx.services.broadcast
        self.admin_feed = ctx.services.admin_feed
        self.rate = ctx.services.media_rate
        self.album_rate = ctx.services.album_rate

    @staticmethod
    def _allowed_type(room: dict, content_type: str) -> bool:
        settings = room.get("settings", {})
        mapping = {"photo": "allow_photo", "video": "allow_video", "document": "allow_files", "animation": "allow_video"}
        key = mapping.get(content_type)
        return bool(settings.get(key, True)) if key else True

    async def handle_message(self, message) -> str:
        uid = int(message.from_user.id) if message.from_user else 0
        if message.media_group_id:
            if not self.album_rate.allow(str(uid)):
                await message.answer("⚠️ Demasiados álbumes en poco tiempo.")
                return "rate_limited"
        elif message.photo or message.video or message.document or message.animation:
            if not self.rate.allow(str(uid)):
                await message.answer("⚠️ Demasiado contenido en poco tiempo.")
                return "rate_limited"

        user = await self.ctx.repositories.user.get(self.ctx.bot_id, uid) if uid else None
        room_id = user.get("active_room_id") if user else None
        room = await self.room_repo.get(self.ctx.bot_id, room_id) if room_id else None
        if room and room.get("status") != "ACTIVE":
            room_id = None
        membership = await self.room_repo.get_membership(self.ctx.bot_id, room["room_id"], uid) if room else None
        if membership and (membership.get("banned") or membership.get("muted")):
            room_id = None
        feed_enabled = await self.ctx.repositories.admin_feed.count(self.ctx.bot_id) > 0

        # Direct Admin Feed is independent from rooms.
        if message.media_group_id:
            item = self.normalize(message)
            if room_id and room and self._allowed_type(room, message.content_type):
                await self.repo.append_group(self.ctx.bot_id, room_id, message.media_group_id, item, uid, int(message.chat.id), message.caption)
                self.ctx.runtime.add_task(self.flush_album(room_id, message.media_group_id, target="room"))
            if feed_enabled:
                await self.repo.append_group(self.ctx.bot_id, ADMIN_FEED_ROOM_KEY, message.media_group_id, item, uid, int(message.chat.id), message.caption)
                self.ctx.runtime.add_task(self.flush_album(ADMIN_FEED_ROOM_KEY, message.media_group_id, target="admin_feed"))
            if not room_id and not feed_enabled:
                await message.answer("🏠 Primero crea o únete a una sala para compartir este álbum.")
                return "no_destination"
            return "album_queued"

        if message.photo or message.video or message.document or message.animation:
            event_id = f"msg:{message.chat.id}:{message.message_id}"
            normalized = self.normalize(message)
            if room_id:
                try:
                    await self.repo.create_event(
                        bot_id=self.ctx.bot_id,
                        room_id=room_id,
                        sender_id=uid,
                        source_chat_id=int(message.chat.id),
                        source_message_id=int(message.message_id),
                        media_group_id=None,
                        type=message.content_type,
                        media_unique_id=normalized["file_unique_id"],
                        status="QUEUED",
                        processing_id=secrets.token_urlsafe(8),
                        event_key=event_id,
                    )
                except Exception as exc:
                    if exc.__class__.__name__ == "DuplicateKeyError":
                        return "duplicate"
                    raise
                if room and not self._allowed_type(room, message.content_type):
                    await message.answer("⛔ Este tipo de multimedia está desactivado en la sala.")
                else:
                    await self.broadcast.enqueue_single(room_id, uid, int(message.chat.id), int(message.message_id), event_id, message)
            if feed_enabled:
                await self.admin_feed.enqueue_message(message)
            if not room_id and not feed_enabled:
                await message.answer("<b>🏠 ELIGE UNA SALA</b>\n\nCrea o únete a una sala para compartir tu contenido con la comunidad.")
                return "no_destination"
            return "queued"

        # Text/other direct content can be mirrored to the admin feed, but is not a room publication.
        if message.text and not message.text.startswith("/"):
            if feed_enabled:
                await self.admin_feed.enqueue_message(message)
                if not room_id:
                    return "admin_feed_only"
            await message.answer("<b>📦 PUBLICACIONES</b>\n\nEn las salas se publican fotos, vídeos, archivos y álbumes.\nUsa el menú para crear o gestionar una sala.")
            return "text"
        return "ignored"

    async def flush_album(self, room_id: str, group_id: str, target: str) -> None:
        await asyncio.sleep(self.ctx.settings.album_debounce_ms / 1000)
        group = await self.repo.get_group(self.ctx.bot_id, room_id, group_id)
        if not group:
            return
        claimed = await self.repo.claim_group(self.ctx.bot_id, room_id, group_id)
        if not claimed:
            return
        items = {int(i["message_id"]): i for i in claimed.get("items", [])}
        claimed["items"] = sorted(items.values(), key=lambda x: int(x["message_id"]))
        if not claimed["items"]:
            return
        if target == "admin_feed":
            await self.admin_feed.enqueue_album(claimed)
        else:
            await self.broadcast.enqueue_album(claimed)

    @staticmethod
    def normalize(message) -> dict:
        if message.photo:
            p = message.photo[-1]
            return {"type": "photo", "file_id": p.file_id, "file_unique_id": p.file_unique_id, "message_id": message.message_id, "caption": message.caption}
        if message.video:
            return {"type": "video", "file_id": message.video.file_id, "file_unique_id": message.video.file_unique_id, "message_id": message.message_id, "caption": message.caption}
        if message.document:
            return {"type": "document", "file_id": message.document.file_id, "file_unique_id": message.document.file_unique_id, "message_id": message.message_id, "caption": message.caption}
        if message.animation:
            return {"type": "animation", "file_id": message.animation.file_id, "file_unique_id": message.animation.file_unique_id, "message_id": message.message_id, "caption": message.caption}
        raise ValueError("Mensaje multimedia no soportado")
