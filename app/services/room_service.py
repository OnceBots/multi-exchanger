from __future__ import annotations

import asyncio
from typing import Any

from app.core.enums import MemberRole, RoomVisibility


class InsufficientReferralCredits(ValueError):
    """A user cannot create a non-admin room without ten available referrals."""

    def __init__(self, available: int, required: int = 10) -> None:
        self.available = max(0, int(available))
        self.required = max(1, int(required))
        missing = max(0, self.required - self.available)
        super().__init__(
            f"Crear una sala cuesta {self.required} referidos válidos. "
            f"Tienes {self.available} disponibles y te faltan {missing}. "
            "Comparte tu enlace personal y consigue nuevos usuarios que inicien el bot desde él."
        )


class RoomService:
    ROOM_REFERRAL_COST = 10

    def __init__(self, repositories, admin_ids=None) -> None:
        self.repositories = repositories
        self.admin_ids = frozenset(int(user_id) for user_id in (admin_ids or ()))
        # The deployment runs a single application process. Serialize attempts
        # for the same owner so simultaneous bot/Mini App requests cannot spend
        # the same referral credits twice in that process.
        self._creation_locks: dict[tuple[int, int], asyncio.Lock] = {}

    async def get_creation_status(self, bot_id: int, user_id: int) -> dict[str, Any]:
        user_id = int(user_id)
        is_admin = user_id in self.admin_ids
        referral_repo = getattr(self.repositories, "referrals", None)
        if referral_repo is None:
            stats = {
                "valid_referrals": 0,
                "rooms_paid": 0,
                "credits_spent": 0,
                "credits_available": 0,
                "credits_required_per_room": self.ROOM_REFERRAL_COST,
            }
        else:
            stats = await referral_repo.stats(int(bot_id), user_id)
        return {**stats, "is_admin": is_admin, "required": self.ROOM_REFERRAL_COST}

    async def create_room(
        self,
        bot_id: int,
        owner_id: int,
        visibility: str,
        max_members: int,
        settings: dict,
        duration_minutes: int,
        password: str | None = None,
    ) -> dict:
        bot_id = int(bot_id)
        owner_id = int(owner_id)
        max_members = max(2, min(int(max_members), 10000))
        if visibility == RoomVisibility.PRIVATE.value:
            password = (password or "").strip()
            if len(password) < 4:
                raise ValueError("Las salas privadas requieren una contraseña de al menos 4 caracteres.")
        else:
            password = None

        lock = self._creation_locks.setdefault((bot_id, owner_id), asyncio.Lock())
        async with lock:
            is_admin = owner_id in self.admin_ids
            referral_cost = 0 if is_admin else self.ROOM_REFERRAL_COST
            if not is_admin:
                status = await self.get_creation_status(bot_id, owner_id)
                available = int(status.get("credits_available", 0))
                if available < self.ROOM_REFERRAL_COST:
                    raise InsufficientReferralCredits(available, self.ROOM_REFERRAL_COST)

            # Referral spending is recorded on the room itself. Available credits
            # are calculated from unique successful referrals minus these rooms,
            # so a retry after a restart cannot double-charge or restore credits.
            return await self.repositories.room.create(
                bot_id,
                owner_id,
                visibility,
                max_members,
                settings,
                max(0, int(duration_minutes)),
                password,
                referral_cost=referral_cost,
            )

    async def can_manage(self, bot_id: int, room_id: str, user_id: int) -> bool:
        member = await self.repositories.room.member(bot_id, room_id, user_id)
        return bool(member and member.get("role") in {MemberRole.OWNER.value, MemberRole.ADMIN.value})

    async def is_owner(self, bot_id: int, room_id: str, user_id: int) -> bool:
        member = await self.repositories.room.member(bot_id, room_id, user_id)
        return bool(member and member.get("role") == MemberRole.OWNER.value)

    async def join(self, bot_id: int, user_id: int, ref: str, password: str | None = None) -> tuple[bool, str, dict | None]:
        room = await self.repositories.room.resolve(bot_id, ref)
        if not room:
            return False, "No encontramos la sala.", None
        ok, message = await self.repositories.room.join(bot_id, room["room_id"], user_id, password=password)
        return ok, message, await self.repositories.room.get(bot_id, room["room_id"])

    async def leave(self, bot_id: int, room_id: str, user_id: int) -> tuple[bool, str]:
        return await self.repositories.room.leave(bot_id, room_id, user_id)

    MEDIA_POLICY_KEYS = (
        "allow_video", "allow_photo", "allow_files", "allow_animation", "allow_albums",
        "allow_audio", "allow_voice", "allow_video_note", "allow_sticker",
    )

    @staticmethod
    def default_media_policy() -> dict:
        """Safe bot-wide policy: rooms publish videos only until a bot admin opts in."""
        return {
            "allow_video": True,
            "allow_photo": False,
            "allow_files": False,
            "allow_animation": False,
            "allow_albums": False,
            "allow_audio": False,
            "allow_voice": False,
            "allow_video_note": False,
            "allow_sticker": False,
        }

    @classmethod
    def normalize_media_policy(cls, value: dict | None) -> dict:
        defaults = cls.default_media_policy()
        value = value if isinstance(value, dict) else {}
        return {key: bool(value.get(key, defaults[key])) for key in cls.MEDIA_POLICY_KEYS}

    @classmethod
    def default_settings(cls) -> dict:
        # Retained in room documents for compatibility. New rooms inherit the
        # current bot-wide media policy; authorization is still enforced globally.
        return cls.default_media_policy()

    @classmethod
    def is_media_allowed(cls, room: dict, media_type: str, policy: dict | None = None) -> bool:
        """Apply the bot-admin-controlled policy; legacy room settings never bypass it."""
        policy = cls.normalize_media_policy(policy)
        key = {
            "photo": "allow_photo", "video": "allow_video", "document": "allow_files",
            "animation": "allow_animation", "audio": "allow_audio", "voice": "allow_voice",
            "video_note": "allow_video_note", "sticker": "allow_sticker",
        }.get(media_type)
        if key is None or not policy.get(key, False):
            return False
        return True

    @staticmethod
    def public_label(room: dict) -> str:
        return "🌎 Pública" if room.get("visibility") == RoomVisibility.PUBLIC.value else "🔒 Privada"
