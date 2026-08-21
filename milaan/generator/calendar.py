"""Deterministic synthetic business-day calendar for 2026."""

from __future__ import annotations

from datetime import date, timedelta


HOLIDAYS_2026 = frozenset({
    date(2026, 1, 1), date(2026, 1, 26), date(2026, 3, 3),
    date(2026, 4, 3), date(2026, 5, 1), date(2026, 8, 15),
    date(2026, 10, 2), date(2026, 10, 20), date(2026, 11, 8),
    date(2026, 12, 25),
})


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS_2026


def add_business_days(day: date, count: int) -> date:
    if count == 0:
        return day
    step = 1 if count > 0 else -1
    remaining = abs(count)
    current = day
    while remaining:
        current += timedelta(days=step)
        if is_business_day(current):
            remaining -= 1
    return current


def business_gap(first: date, second: date) -> int:
    """Return the order-agnostic number of business-day steps between dates."""
    low, high = sorted((first, second))
    gap = 0
    current = low
    while current < high:
        current += timedelta(days=1)
        if is_business_day(current):
            gap += 1
    return gap
