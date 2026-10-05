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
