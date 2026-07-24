"""Shared statutory workday calendar for recent-window statistics and trends."""

from __future__ import annotations

from datetime import date, timedelta

from chinese_calendar import is_workday


def is_service_day(value: date) -> bool:
    """Return whether a date is a statutory workday, including make-up days.

    ``chinese-calendar`` encodes the State Council annual holiday and make-up
    workday arrangements. Its published range can lag a newly introduced year;
    in that exceptional case, retain the previous weekday rule rather than
    failing a local query or an incremental aggregation.
    """

    try:
        return bool(is_workday(value))
    except NotImplementedError:
        return value.weekday() < 5


def recent_service_days(anchor: date, *, count: int) -> list[date]:
    """Return the last ``count`` statutory workdays ending at ``anchor``."""

    if count < 1:
        raise ValueError("工作日数量必须为正整数")
    days: list[date] = []
    current = anchor
    while len(days) < count:
        if is_service_day(current):
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)
