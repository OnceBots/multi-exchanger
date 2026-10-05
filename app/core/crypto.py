from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self.fernet = Fernet(key.encode("utf-8"))
        except Exception as exc:
            raise ValueError("BOT_TOKEN_ENCRYPTION_KEY no es una clave Fernet válida") from exc

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("No se pudo descifrar un secreto almacenado") from exc
