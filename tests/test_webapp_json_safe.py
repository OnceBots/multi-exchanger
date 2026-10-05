from pathlib import Path


def test_webapp_uses_json_safe_payloads():
    source = Path('app/web/webapp.py').read_text(encoding='utf-8')
    assert 'def json_safe(value):' in source
    assert 'return json_safe(response)' in source
    assert 'return json_safe({"ok": True, "profile": profile})' in source


def test_child_ui_distinguishes_auth_errors_from_backend_errors():
    source = Path('app/web/static/child.html').read_text(encoding='utf-8')
    assert 'if(e.status===401)' in source
    assert "Telegram conectado'" in source
