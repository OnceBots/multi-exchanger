from pathlib import Path


def test_master_webhook_does_not_reference_undefined_bot_id():
    source = Path(__file__).parents[1].joinpath("app", "web", "webhook.py").read_text(encoding="utf-8")
    master = source.split('@router.post("/telegram/webhook/{bot_id}")', 1)[0]
    assert 'logger.info("child_webhook_received bot_id=%s update_id=%s keys=%s", bot_id' not in master
    assert 'logger.info("master_webhook_received update_id=%s kind=%s keys=%s", update_id, update_kind' in master
