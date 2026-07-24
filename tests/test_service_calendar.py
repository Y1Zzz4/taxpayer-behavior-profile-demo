from datetime import date

from taxpayer_profile.service_calendar import is_service_day, recent_service_days


def test_service_calendar_excludes_holiday_and_keeps_makeup_workday() -> None:
    """The service window follows statutory leave and make-up workdays."""

    assert is_service_day(date(2026, 1, 1)) is False
    assert is_service_day(date(2026, 1, 4)) is True  # Sunday make-up workday.
    assert recent_service_days(date(2026, 1, 5), count=3) == [
        date(2025, 12, 31),
        date(2026, 1, 4),
        date(2026, 1, 5),
    ]
