from __future__ import annotations

import asyncio
import logging


class MediaService:
    def __init__(self, ctx, rooms) -> None:
        self.ctx = ctx
        self.rooms = rooms
        self.logger = logging.getLogger(f"media.{ctx.bot_id}")
        self.album_tasks: dict[tuple[str, int, str], asyncio.Task] = {}

    def media_from_message(self, message):
        if message.photo:
            return "photo", message.photo[-1].file_id
        if message.video:
            return "video", message.video.file_id
        if message.document:
            return "document", message.document.file_id
        if message.animation:
            return "animation", message.animation.file_id
        return None, None

    async def handle_message(self, message) -> bool:
        if not message.from_user:
            return False
        user_id = int(message.from_user.id)
        session = await self.ctx.repositories.session.get(self.ctx.bot_id, user_id)
        room_id = (session or {}).get("data", {}).get("active_room_id")
        if not room_id:
            rooms = await self.ctx.repositories.room.list_for_user(self.ctx.bot_id, user_id, 1)
            room_id = rooms[0]["room_id"] if rooms else None
        if not room_id:
            media_type, file_id = self.media_from_message(message)
            if media_type and self.ctx.admin_feed_queue is not None and message.chat.type == "private":
                await self.ctx.admin_feed_queue.put({"sender_id": user_id, "media_type": media_type, "file_id": file_id, "caption": message.caption, "direct": True})
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
        if not self.rooms.is_media_allowed(room, media_type):
            await message.answer("🚫 Ese tipo de archivo está deshabilitado en esta sala.")
            return False

        if message.media_group_id and media_type in {"photo", "video"}:
            item = {"message_id": int(message.message_id), "file_id": file_id, "type": media_type}
            await self.ctx.repositories.media.append_group(self.ctx.bot_id, room_id, str(message.media_group_id), item, user_id, message.caption)
            key = (room_id, user_id, str(message.media_group_id))
            old = self.album_tasks.pop(key, None)
            if old:
                old.cancel()
            if self.ctx.task_registry:
                task = self.ctx.task_registry.create(self._finalize_album(key), f"album:{self.ctx.bot_id}:{message.media_group_id}")
            else:
                task = asyncio.create_task(self._finalize_album(key), name=f"album:{self.ctx.bot_id}:{message.media_group_id}")
            self.album_tasks[key] = task
            return True

        await self.ctx.broadcast_queue.put({
            "room_id": room_id,
            "chat_id": int(message.chat.id),
            "sender_id": user_id,
            "message_id": int(message.message_id),
            "media_type": media_type,
            "file_id": file_id,
            "caption": message.caption,
        })
        await self.ctx.admin_feed_queue.put({"sender_id": user_id, "media_type": media_type, "file_id": file_id, "caption": message.caption})
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
                await self.ctx.admin_feed_queue.put({"sender_id": sender_id, "media_type": "album", "file_id": first.get("file_id"), "caption": group.get("caption"), "group": group})
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("album_finalize_failed")
        finally:
            self.album_tasks.pop(key, None)
