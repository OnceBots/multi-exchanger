from __future__ import annotations

import asyncio
import logging

from app.core.datetime import utcnow
from app.core.enums import BotStatus
from app.core.models import BotInfo


class ChildBotProvisioner:
    """Register new child bots and safely reactivate archived ones.

    Reusing an archived bot ID preserves its owner and all non-lifecycle
    configuration/data in MongoDB. Only secrets and runtime state are renewed.
    """

    def __init__(self, manager) -> None:
        self.manager = manager
        self.logger = logging.getLogger("provisioner")
        self._lock = asyncio.Lock()

    @staticmethod
    def _is_archived(document: dict) -> bool:
        status = str(document.get("status") or "").upper()
        lifecycle = (document.get("config") or {}).get("lifecycle") or {}
        lifecycle_archived = str(lifecycle.get("mode") or "").upper() == "ARCHIVED"
        return status == BotStatus.ARCHIVED.value or (document.get("enabled") is False and lifecycle_archived)

    async def create(self, token: str, owner_id: int, metadata: dict | None = None) -> BotInfo:
        token = token.strip()
        bot_id, username, first_name = await self.manager.token_service.validate_token(token)

        # Serialize create/reactivate operations in this process. MongoDB's
        # unique bot_id index remains the cross-process duplicate guard.
        async with self._lock:
            existing = await self.manager.repositories.bots.get(bot_id)
            now = utcnow()
            reactivated = False
            original_owner_id = int(owner_id)
            secret = self.manager.token_service.new_webhook_secret()

            if existing:
                if not self._is_archived(existing):
                    raise ValueError("Ese bot ya está registrado en la plataforma.")

                stored_owner_id = existing.get("owner_id")
                original_owner_id = int(stored_owner_id if stored_owner_id is not None else owner_id)
                admin_ids = set(getattr(self.manager.settings, "admin_ids", ()) or ())
                if int(owner_id) != original_owner_id and int(owner_id) not in admin_ids:
                    raise ValueError(
                        "Este bot archivado pertenece a otro propietario. "
                        "Solo el propietario original o un administrador puede recuperarlo."
                    )

                # An archived record should not have a live runtime, but clean up
                # any inconsistent in-memory runtime before reusing its bot_id.
                if bot_id in self.manager.registry:
                    await self.manager.stop_bot(bot_id)
                    await self.manager.repositories.bots.archive(bot_id, "reactivation_reset")

                updated = await self.manager.repositories.bots.reactivate_archived(
                    bot_id,
                    {
                        "username": username,
                        "first_name": first_name,
                        "status": BotStatus.STARTING.value,
                        "enabled": True,
                        "token_encrypted": self.manager.token_service.encrypt(token),
                        "webhook_secret_encrypted": self.manager.token_service.encrypt(secret),
                        "last_error": None,
                        "updated_at": now,
                        "reactivated_at": now,
                    },
                )
                if not updated:
                    # It may have been modified concurrently. Never insert a
                    # second document for the same bot ID.
                    latest = await self.manager.repositories.bots.get(bot_id)
                    if latest and not self._is_archived(latest):
                        raise ValueError("Ese bot ya está registrado en la plataforma.")
                    raise RuntimeError("No se pudo recuperar el bot archivado; vuelve a intentarlo.")
                reactivated = True
                self.logger.info("archived_child_reactivated bot_id=%s owner_id=%s actor_id=%s", bot_id, original_owner_id, owner_id)
            else:
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
            # Ephemeral UI hint; deliberately not persisted in bot config.
            info.config["_reactivated_in_this_request"] = reactivated
            await self.manager.notify_admins_new_bot(info, original_owner_id, reactivated=reactivated)
            await self.manager.notify_creator_new_bot(info, original_owner_id)
            return info
