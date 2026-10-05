from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


_ITERATIONS = 180_000


def hash_password(password: str) -> dict[str, str | int]:
    password = (password or "").strip()
    if not password:
        raise ValueError("La contraseña no puede estar vacía.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return {
        "algorithm": "pbkdf2-sha256",
        "iterations": _ITERATIONS,
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "hash": base64.urlsafe_b64encode(digest).decode("ascii"),
    }


def verify_password(password: str, record: dict | None) -> bool:
    if not record or not password:
        return False
    try:
        salt = base64.urlsafe_b64decode(str(record["salt"]).encode("ascii"))
        expected = base64.urlsafe_b64decode(str(record["hash"]).encode("ascii"))
        iterations = int(record.get("iterations", _ITERATIONS))
        actual = hashlib.pbkdf2_hmac("sha256", password.strip().encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False
