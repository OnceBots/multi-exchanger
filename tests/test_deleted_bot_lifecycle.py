from pathlib import Path


def test_manager_archives_telegram_unauthorized_children():
    source = Path("app/bot/manager.py").read_text(encoding="utf-8")
    assert 'archive_bot(bot_id, reason="telegram_deleted")' in source
    assert "child_archived_after_telegram_unauthorized" in source


def test_deleted_bot_message_is_distinct_from_manual_delete_instructions():
    source = Path("app/services/bot_lifecycle.py").read_text(encoding="utf-8")
    assert 'reason == "telegram_deleted"' in source
    assert "Un bot eliminado en Telegram no puede volver a funcionar con ese mismo token" in source
