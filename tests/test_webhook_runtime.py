from pathlib import Path


def test_master_webhook_reconciliation_present() -> None:
    root = Path(__file__).resolve().parents[1]
    manager = (root / "app" / "bot" / "manager.py").read_text(encoding="utf-8")
    webhook = (root / "app" / "web" / "webhook.py").read_text(encoding="utf-8")
    assert "reconcile_master_webhook" in manager
    assert "pending_delivery_stall" in manager
    assert "last_master_webhook_received_at" in manager
    assert "master_webhook_received" in webhook
