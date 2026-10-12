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


def test_room_share_deep_link_is_present():
    source = Path("app/bot/child_handlers.py").read_text(encoding="utf-8")
    assert "?start=room_" in source
    assert "len(code) != 7" in source
    assert "t.me/share/url" in source



def test_room_identity_is_immutable():
    source = Path("app/db/repositories/rooms.py").read_text(encoding="utf-8")
    assert 'fields.pop("name", None)' in source
    assert 'fields.pop("invite_code", None)' in source


