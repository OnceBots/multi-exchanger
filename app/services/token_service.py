from __future__ import annotations

import secrets

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

from app.core.crypto import SecretBox


class InvalidBotTokenError(ValueError):
    pass


class TokenService:
    def __init__(self, box: SecretBox, secret_length: int = 32) -> None:
        self.box = box
        self.secret_length = secret_length

    @staticmethod
    def validate_format(token: str) -> None:
        value = token.strip()
        if ":" not in value or len(value) < 30:
            raise InvalidBotTokenError("El token no tiene un formato válido.")

    async def validate_token(self, token: str) -> tuple[int, str, str]:
        self.validate_format(token)
        bot = Bot(token=token.strip())
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
