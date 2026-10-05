from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _ub64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> dict | None:
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        auth_date = int(parsed.get("auth_date", "0"))
        if auth_date <= 0 or abs(int(time.time()) - auth_date) > max_age_seconds:
            return None
        check_string = "\n".join(f"{key}={parsed[key]}" for key in sorted(parsed))
        secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            return None
        user = parsed.get("user")
        if not user:
            return None
        value = json.loads(user)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def extract_init_data(request) -> str:
    """Accept Telegram raw initData from the two common header conventions."""
    value = (request.headers.get("X-Telegram-Init-Data") or "").strip()
    if value:
        return value

    authorization = (request.headers.get("Authorization") or "").strip()
    if not authorization:
        return ""
    if authorization.startswith("tma "):
        return authorization[4:].strip()
    return authorization


def make_launch_token(secret: str, bot_id: int, user_id: int, ttl_seconds: int = 900) -> str:
    """Create a short-lived signed fallback token for Mini App launches.

    Telegram initData remains the preferred authentication mechanism. The launch
    token only exists to make personalized WebApp buttons resilient when a client
    does not expose raw initData to JavaScript.
    """
    expires = int(time.time()) + max(60, int(ttl_seconds))
    nonce = secrets.token_urlsafe(12)
    payload = f"{int(bot_id)}:{int(user_id)}:{expires}:{nonce}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{expires}.{_b64(str(user_id).encode())}.{nonce}.{_b64(signature)}"


def validate_launch_token(secret: str, token: str, bot_id: int, max_age_seconds: int = 900) -> int | None:
    try:
        expires_s, user_b64, nonce, signature_b64 = token.split(".", 3)
        expires = int(expires_s)
        user_id = int(_ub64(user_b64).decode("ascii"))
        now = int(time.time())
        if expires < now or expires - now > max(max_age_seconds, 60) + 30:
            return None
        payload = f"{int(bot_id)}:{user_id}:{expires}:{nonce}"
        expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        received = _ub64(signature_b64)
        if not hmac.compare_digest(expected, received):
            return None
        return user_id
    except Exception:
        return None


def authenticate_webapp_request(
    request,
    *,
    bot_id: int,
    bot_token: str,
    launch_secret: str,
    max_age_seconds: int,
) -> tuple[dict | None, str]:
    """Authenticate a Mini App request using Telegram initData first, then launch token."""
    init_data = extract_init_data(request)
    if init_data:
        user = validate_init_data(init_data, bot_token, max_age_seconds)
        if user and user.get("id"):
            return user, "init_data"

    launch = (request.headers.get("X-Mini-App-Launch-Token") or "").strip()
    if not launch:
        # Backward compatibility with previously generated buttons. New buttons
        # keep the token in the URL fragment to avoid access-log leakage.
        launch = (request.query_params.get("launch") or "").strip()
    if launch:
        user_id = validate_launch_token(launch_secret, launch, bot_id, max_age_seconds=min(max_age_seconds, 900))
        if user_id:
            return {"id": user_id}, "launch_token"

    return None, "none"
