from __future__ import annotations

import logging

from app.core.datetime import utcnow
from app.core.enums import BotStatus
from app.core.models import BotInfo
from app.services.token_service import InvalidBotTokenError


class ChildBotProvisioner:
    def __init__(self, manager) -> None:
        self.manager = manager
        self.logger = logging.getLogger("provisioner")

    async def create(self, token: str, owner_id: int, metadata: dict | None = None) -> BotInfo:
        token = token.strip()
        bot_id, username, first_name = await self.manager.token_service.validate_token(token)
        if await self.manager.repositories.bots.exists(bot_id):
            raise ValueError("Ese bot ya está registrado en la plataforma.")

        now = utcnow()
        secret = self.manager.token_service.new_webhook_secret()
        document = {
            "bot_id": bot_id,
            "username": username,
            "first_name": first_name,
            "owner_id": int(owner_id),
            "status": BotStatus.STARTING.value,
            "enabled": True,
            "token_encrypted": self.manager.token_service.encrypt(token),
            "webhook_secret_encrypted": self.manager.token_service.encrypt(secret),
            "restart_count": 0,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
            "config": metadata or {},
        }
        try:
            await self.manager.repositories.bots.create(document)
        except Exception as exc:
            if exc.__class__.__name__ == "DuplicateKeyError":
                raise ValueError("Ese bot ya está registrado en la plataforma.") from exc
            raise

        try:
            await self.manager.start_bot(bot_id)
        except Exception as exc:
            self.logger.exception("child_provision_start_failed bot_id=%s", bot_id)
            raise RuntimeError(f"El token es válido, pero no se pudo activar el bot: {exc}") from exc

        info = await self.manager.get_info(bot_id)
        await self.manager.notify_admins_new_bot(info, owner_id)
        return info
