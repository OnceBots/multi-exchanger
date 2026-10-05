from datetime import datetime, timezone

from app.core.datetime import age_seconds, ensure_utc, utcnow


def test_naive_is_treated_as_utc():
    value = datetime(2026, 1, 1, 12, 0, 0)
    normalized = ensure_utc(value)
    assert normalized is not None
    assert normalized.tzinfo is not None
    assert normalized.tzinfo == timezone.utc


def test_aware_is_preserved_in_utc():
    value = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert ensure_utc(value) == value


def test_age_never_raises_for_mixed_datetime():
    now = utcnow()
    naive = now.replace(tzinfo=None)
    assert age_seconds(naive, now) == 0.0
