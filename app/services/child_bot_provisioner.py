from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.enums import BotStatus
from app.core.exceptions import BotAlreadyRunningError
from app.core.models import BotInfo
from pymongo.errors import DuplicateKeyError


class ChildBotProvisioner:
    """Provisiona hijos siguiendo el flujo probado del proyecto de referencia.

    Flujo: validar token -> comprobar duplicado -> persistir configuración ->
    arrancar runtime -> notificar. El runtime continúa usando webhook en esta
    plataforma; no se copia el polling del proyecto de referencia.
    """

    def __init__(self, manager) -> None:
        self.manager = manager
        self.logger = logging.getLogger("bot.provisioner")

    async def create(
        self,
        token: str,
        owner_id: int,
        metadata: dict | None = None,
    ) -> BotInfo:
        token = (token or "").strip()
        if not token:
            raise ValueError("El token está vacío")

        self.logger.info("child_create_requested owner_id=%s", owner_id)

        # 1) Validación real contra Telegram, antes de crear el documento.
        bot_id, username = await self.manager.token_service.validate_token(token)
        self.logger.info("child_token_validated bot_id=%s username=@%s", bot_id, username)

        # 2) Claim atómico del bot_id dentro del proceso.
        async with self.manager.lock:
            existing = await self.manager.repositories.bots.get(bot_id)
            if existing:
                if int(existing.get("owner_id", 0)) == int(owner_id):
                    raise BotAlreadyRunningError("Ese bot ya está registrado en tu cuenta")
                raise ValueError("Ese bot ya está registrado en la plataforma")

        secret = self.manager.token_service.new_webhook_secret()
        now = datetime.now(timezone.utc)
        merged_config = {
            "features": {
                "media": True,
                "rooms": True,
                "webapp": True,
                "admin_feed": True,
            },
            "language": "es",
            "max_members_default": 100,
        }

        document = {
            "bot_id": bot_id,
            "username": username,
            "owner_id": int(owner_id),
            "token_encrypted": self.manager.token_service.encrypt(token),
            "webhook_secret_encrypted": self.manager.token_service.encrypt(secret),
            "enabled": True,
            "status": BotStatus.STARTING.value,
            "config": merged_config,
            "metadata": dict(metadata or {}),
            "restart_count": 0,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }

        # 3) Persistir el tenant en STARTING para que un restart pueda
        # continuar/supervisarlo sin depender de memoria.
        try:
            await self.manager.repositories.bots.create(document)
        except DuplicateKeyError as exc:
            raise ValueError("Ese bot ya está registrado en la plataforma") from exc

        try:
            # 4) Arrancar el runtime. En esta plataforma se usa webhook.
            await self.manager.start_bot(bot_id)
        except Exception:
            self.logger.exception("child_create_start_failed bot_id=%s", bot_id)
            # No eliminamos el documento: queda disponible para inspección y
            # reintento del administrador/supervisor.
            await self.manager.repositories.bots.update_status(
                bot_id,
                BotStatus.ERROR.value,
                "No se pudo iniciar el runtime del bot hijo",
            )
            raise

        stored = await self.manager.repositories.bots.get(bot_id)
        info = BotInfo.from_document(stored or document)
        await self.manager.notify_admins_new_bot(info, owner_id)
        self.logger.info("child_create_completed bot_id=%s username=@%s", bot_id, username)
        return info
