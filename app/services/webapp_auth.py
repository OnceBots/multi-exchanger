from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, unquote


class WebAppAuthError(ValueError):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> dict:
    if not init_data:
        raise WebAppAuthError("initData ausente")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise WebAppAuthError("hash ausente")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise WebAppAuthError("firma inválida")
    auth_date = int(pairs.get("auth_date", "0"))
    if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
        raise WebAppAuthError("initData expirada")
    user_raw = pairs.get("user")
    if not user_raw:
        raise WebAppAuthError("usuario ausente")
    return json.loads(unquote(user_raw))
