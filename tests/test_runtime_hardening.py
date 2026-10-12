from pathlib import Path


def test_render_uses_docker_runtime_and_readiness_without_hardcoded_port():
    text = Path("render.yaml").read_text(encoding="utf-8")
    assert "runtime: docker" in text
    assert "healthCheckPath: /ready" in text
    assert "key: PORT" not in text


def test_child_menu_uses_telegram_chat_buttons():
    text = Path("app/bot/child_handlers.py").read_text(encoding="utf-8")
    assert 'callback_data="room:create"' in text
    assert "WebAppInfo" not in text


def test_manager_has_no_webapp_menu():
    text = Path("app/bot/manager.py").read_text(encoding="utf-8")
    assert "MenuButtonWebApp" not in text
    assert "set_chat_menu_button" not in text


def test_manager_skips_callback_queries_during_replay() -> None:
    source = Path("app/bot/manager.py").read_text(encoding="utf-8")
    assert "stale_callback_replay_skipped" in source
    assert 'if payload.get("callback_query"):' in source


def test_manager_ignores_stale_callback_query_errors() -> None:
    source = Path("app/bot/manager.py").read_text(encoding="utf-8")
    assert "stale_callback_ignored" in source
    assert "query is too old" in source
