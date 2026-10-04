import hashlib, hmac, json, time
from urllib.parse import urlencode
from app.services.webapp_auth import validate_init_data


def test_webapp_auth_roundtrip():
    token = "123456:ABC"
    user = json.dumps({"id": 42, "first_name": "Test"}, separators=(",", ":"))
    pairs = {"auth_date": str(int(time.time())), "query_id": "Q", "user": user}
    check = "\n".join(f"{k}={v}" for k,v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    data = urlencode({**pairs, "hash": digest})
    result = validate_init_data(data, token, 60)
    assert result["id"] == 42
