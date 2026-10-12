from __future__ import annotations

import logging
import secrets

import httpx
from aiogram import Bot

from app.bot.api_client import build_bot
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

from app.core.crypto import SecretBox


class InvalidBotTokenError(ValueError):
    pass


class TokenService:
    def __init__(self, box: SecretBox, secret_length: int = 32, settings=None) -> None:
        self.box = box
        self.secret_length = secret_length
        self.settings = settings
        self.logger = logging.getLogger("bot.token_service")

    async def _release_cloud_api_session(self, token: str) -> None:
        """Move a token off Telegram's hosted Bot API before validating it locally.

        This is required for tokens being migrated to a self-hosted Bot API server.
        Errors are not considered conclusive here: the token may already be using
        the local server. The subsequent local getMe remains the source of truth.
        Never log the token or the request URL, which contains the token.
        """
        if self.settings is None or not getattr(self.settings, "telegram_api_local_mode", False):
            return
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"https://api.telegram.org/bot{token}/logOut")
                payload = response.json()
                if payload.get("ok"):
                    self.logger.info("telegram_cloud_session_released")
                else:
                    self.logger.info("telegram_cloud_logout_not_confirmed; validating_local_api")
        except (httpx.HTTPError, ValueError):
            # The local getMe below confirms if this bot can actually be used.
            self.logger.warning("telegram_cloud_logout_unreachable; validating_local_api")

    @staticmethod
    def validate_format(token: str) -> None:
        value = token.strip()
        if ":" not in value or len(value) < 30:
            raise InvalidBotTokenError("El token no tiene un formato válido.")

    async def validate_token(self, token: str) -> tuple[int, str, str]:
        self.validate_format(token)
        await self._release_cloud_api_session(token.strip())
        bot = build_bot(token, self.settings) if self.settings is not None else Bot(token=token.strip())
        try:
            me = await bot.get_me()
            return int(me.id), me.username or "", me.first_name or "Bot"
        except (TelegramUnauthorizedError, TelegramNetworkError) as exc:
            raise InvalidBotTokenError("Telegram no pudo validar el token.") from exc
        except Exception as exc:
            raise InvalidBotTokenError("El token fue rechazado por Telegram.") from exc
        finally:
            await bot.session.close()

    def encrypt(self, value: str) -> str:
        return self.box.encrypt(value)

    def decrypt(self, value: str) -> str:
        return self.box.decrypt(value)

    def new_webhook_secret(self) -> str:
        return secrets.token_urlsafe(self.secret_length)
