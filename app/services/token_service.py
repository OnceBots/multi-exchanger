from __future__ import annotations

import secrets

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramUnauthorizedError

from app.core.crypto import SecretBox
from app.core.exceptions import InvalidBotTokenError


class TokenService:
    def __init__(self, box: SecretBox, secret_length: int = 32) -> None:
        self.box = box
        self.secret_length = secret_length

    @staticmethod
    def validate_format(token: str) -> None:
        if ":" not in token or len(token) < 30:
            raise InvalidBotTokenError("Formato de token inválido")

    async def validate_token(self, token: str) -> tuple[int, str]:
        self.validate_format(token)
        bot = Bot(token=token)
        try:
            me = await bot.get_me()
            return int(me.id), me.username or ""
        except (TelegramBadRequest, TelegramUnauthorizedError, TelegramNetworkError) as exc:
            raise InvalidBotTokenError("Telegram rechazó el token o no respondió") from exc
        finally:
            await bot.session.close()

    def encrypt(self, value: str) -> str:
        return self.box.encrypt(value)

    def decrypt(self, value: str) -> str:
        return self.box.decrypt(value)

    def new_webhook_secret(self) -> str:
        return secrets.token_urlsafe(self.secret_length)
