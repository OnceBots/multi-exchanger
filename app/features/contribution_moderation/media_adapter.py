from __future__ import annotations

"""Helpers to register existing Telegram media without replacing MediaService."""


def media_fingerprint(message) -> str | None:
    media = getattr(message, "photo", None) or getattr(message, "video", None) or getattr(message, "document", None) or getattr(message, "animation", None)
    if not media:
        return None
    try:
        return str(getattr(media[-1] if isinstance(media, (list, tuple)) else media, "file_unique_id", None) or "") or None
    except Exception:
        return None


def album_fingerprint(items: list[dict]) -> str | None:
    values = sorted(str(item.get("file_unique_id") or item.get("file_id") or "") for item in items)
    values = [v for v in values if v]
    return "album:" + "|".join(values) if values else None


async def register_single_message(service, bot_id: int, message, room_id: str, media_type: str, file_id: str, event_key: str | None = None) -> dict:
    return await service.register_contribution(
        bot_id=bot_id,
        uploader_id=int(message.from_user.id),
        room_id=room_id,
        media_type=media_type,
        file_id=file_id,
        fingerprint=media_fingerprint(message),
        caption=getattr(message, "caption", None),
        event_key=event_key,
    )
