from datetime import date

from qt_app.answer import daily_factor


def test_the_daily_factor_is_shared_within_a_day_and_changes_between_days():
    day, next_day = date(2026, 11, 10), date(2026, 11, 11)
    assert daily_factor("taxes", day) == daily_factor("taxes", day)
    assert daily_factor("taxes", day) != daily_factor("taxes", next_day)
    assert daily_factor("taxes", day) != daily_factor("fees", day)
