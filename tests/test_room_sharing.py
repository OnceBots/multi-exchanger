from __future__ import annotations

from pathlib import Path


def test_room_code_is_seven_alphanumeric_characters():
    source = Path("app/db/repositories/rooms.py").read_text(encoding="utf-8")
    assert "range(7)" in source
    assert "string.ascii_uppercase + string.digits" in source
    assert "invite_code" in source


def test_room_creation_flow_has_no_description_step():
    source = Path("app/bot/child_handlers.py").read_text(encoding="utf-8")
    assert "room_create_description" not in source
    assert "roomedit:desc" not in source
    assert "room_create_name" not in source
    assert "roomedit:name" not in source
    assert "name: str" not in Path("app/web/webapp.py").read_text(encoding="utf-8")


def test_room_share_deep_link_is_present():
    source = Path("app/bot/child_handlers.py").read_text(encoding="utf-8")
    assert "?start=room_" in source
    assert "len(code) != 7" in source
    assert "t.me/share/url" in source


def test_webapp_rejects_description_field():
    source = Path("app/web/webapp.py").read_text(encoding="utf-8")
    assert 'ConfigDict(extra="forbid")' in source
    assert 'description: str' not in source


def test_mini_app_contains_share_button_and_no_description_input():
    source = Path("app/web/static/child.html").read_text(encoding="utf-8")
    assert '🔗 Compartir' in source
    assert 'roomDesc' not in source
    assert 'roomName' not in source
    assert 'Código aleatorio de 7 caracteres' in source


def test_room_creation_has_no_name_field():
    from pathlib import Path
    webapp = Path("app/web/webapp.py").read_text(encoding="utf-8")
    service = Path("app/services/room_service.py").read_text(encoding="utf-8")
    repo = Path("app/db/repositories/rooms.py").read_text(encoding="utf-8")
    assert "name: str" not in webapp
    assert "owner_id: int, visibility: str" in service
    assert '"invite_code": invite_code' in repo
    assert '"name": invite_code' in repo


def test_mini_app_auth_headers_are_not_async():
    source = Path("app/web/static/child.html").read_text(encoding="utf-8")
    assert "function authHeaders()" in source
    assert "async function authHeaders()" not in source
    assert "X-Telegram-Init-Data" in source
    assert "X-Mini-App-Launch-Token" in source


def test_room_identity_is_immutable():
    source = Path("app/db/repositories/rooms.py").read_text(encoding="utf-8")
    assert 'fields.pop("name", None)' in source
    assert 'fields.pop("invite_code", None)' in source


def test_mini_app_html_is_no_cache():
    source = Path("app/web/webapp.py").read_text(encoding="utf-8")
    assert 'Cache-Control' in source
    assert 'no-store' in source
