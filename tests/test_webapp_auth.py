import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from types import SimpleNamespace

from app.services.webapp_auth import (
    authenticate_webapp_request,
    make_launch_token,
    validate_init_data,
)


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


def test_launch_token_roundtrip_without_init_data():
    secret = "master-secret-for-tests"
    token = make_launch_token(secret, 8900, 42, ttl_seconds=300)
    request = SimpleNamespace(headers={}, query_params={"launch": token})
    user, source = authenticate_webapp_request(request, bot_id=8900, bot_token="unused", launch_secret=secret, max_age_seconds=300)
    assert user == {"id": 42}
    assert source == "launch_token"


def test_query_user_id_fallback():
    from starlette.requests import Request
    from app.services.webapp_auth import authenticate_webapp_request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/child/rooms",
        "headers": [],
        "query_string": b"bot_id=123&user_id=456&id=456",
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
        "scheme": "http",
    }
    request = Request(scope)
    user, source = authenticate_webapp_request(
        request, bot_id=123, bot_token="unused", launch_secret="secret", max_age_seconds=86400
    )
    assert user == {"id": 456}
    assert source == "query_user_id"
