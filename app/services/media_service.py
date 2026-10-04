from __future__ import annotations

import asyncio
import secrets


class MediaService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.repo = ctx.repositories.media
        self.room_repo = ctx.repositories.room
        self.broadcast = ctx.services.broadcast
        self.rate = ctx.services.media_rate
        self.album_rate = ctx.services.album_rate

    async def handle_message(self, message) -> str:
        if not (message.photo or message.video or message.document or message.animation):
            return "ignored"
        uid = int(message.from_user.id)
        if message.media_group_id:
            if not self.album_rate.allow(str(uid)):
                await message.answer("⚠️ Demasiados álbumes en poco tiempo.")
                return "rate_limited"
        elif not self.rate.allow(str(uid)):
            await message.answer("⚠️ Demasiado contenido en poco tiempo.")
            return "rate_limited"
        user = await self.ctx.repositories.user.get(self.ctx.bot_id, uid)
        room_id = user.get("active_room_id") if user else None
        if not room_id:
            await message.answer("Primero crea o únete a una sala con /rooms o /create_room.")
            return "no_room"
        if message.media_group_id:
            item = self.normalize(message)
            await self.repo.append_group(self.ctx.bot_id, room_id, message.media_group_id, item, uid, int(message.chat.id), message.caption)
            self.ctx.runtime.add_task(self.flush_album(room_id, message.media_group_id))
            return "album"
        event_id = f"msg:{message.chat.id}:{message.message_id}"
        try:
            await self.repo.create_event(bot_id=self.ctx.bot_id, room_id=room_id, sender_id=uid, source_chat_id=int(message.chat.id), source_message_id=int(message.message_id), media_group_id=None, type=message.content_type, media_unique_id=self.normalize(message)["file_unique_id"], status="QUEUED", processing_id=secrets.token_urlsafe(8), event_key=event_id)
        except Exception as exc:
            if exc.__class__.__name__ == "DuplicateKeyError":
                return "duplicate"
            raise
        await self.broadcast.enqueue_single(room_id, uid, int(message.chat.id), int(message.message_id), event_id, message)
        return "queued"

    async def flush_album(self, room_id: str, group_id: str) -> None:
        await asyncio.sleep(self.ctx.settings.album_debounce_ms / 1000)
        group = await self.repo.get_group(self.ctx.bot_id, room_id, group_id)
        if not group:
            return
        claimed = await self.repo.claim_group(self.ctx.bot_id, room_id, group_id)
        if not claimed:
            return
        items = {int(i["message_id"]): i for i in group.get("items", [])}
        group["items"] = list(items.values())
        if not group["items"]:
            return
        await self.broadcast.enqueue_album(group)

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
