from datetime import datetime, timezone

from app.core.models import ensure_utc


def test_ensure_utc_converts_naive_datetime():
    value = ensure_utc(datetime(2026, 10, 5, 3, 58, 0))
    assert value is not None
    assert value.tzinfo == timezone.utc


def test_ensure_utc_preserves_aware_datetime():
    value = ensure_utc(datetime(2026, 10, 5, 3, 58, 0, tzinfo=timezone.utc))
    assert value is not None
    assert value.tzinfo == timezone.utc
