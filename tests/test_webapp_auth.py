import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from app.services.webapp_auth import validate_init_data


def make_init_data(token: str, user: dict) -> str:
    values = {"auth_date": str(int(time.time())), "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False)}
    check = "\n".join(f"{k}={values[k]}" for k in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_webapp_auth():
    token = "test-bot-token"
    payload = make_init_data(token, {"id": 42, "first_name": "Test"})
    user = validate_init_data(payload, token, 60)
    assert user and user["id"] == 42


def test_webapp_auth_rejects_bad_hash():
    token = "test-bot-token"
    payload = make_init_data(token, {"id": 42, "first_name": "Test"}).replace("hash=", "hash=deadbeef")
    assert validate_init_data(payload, token, 60) is None
