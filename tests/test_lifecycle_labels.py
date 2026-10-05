from app.services.bot_lifecycle import schedule_label


def test_schedule_label_minutes_and_hours():
    assert schedule_label(5) == "5 min"
    assert schedule_label(60) == "1 h"
    assert schedule_label(90) == "1 h 30 min"
