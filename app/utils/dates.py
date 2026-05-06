"""Date / week / cutoff utilities — IST-aware (Phase 1 single-TZ).

Phase 1 uses one timezone (IST = Asia/Kolkata) per NFR. Per-project TZ
support is deferred. Week boundary is configurable per project but defaults
to Monday.
"""
from __future__ import annotations
from datetime import datetime, date, time, timedelta
from typing import Optional, Literal
import pytz


IST = pytz.timezone("Asia/Kolkata")


def now_ist() -> datetime:
    """Current time in IST (timezone-aware)."""
    return datetime.now(IST)


def today_ist() -> date:
    """Today's date in IST."""
    return now_ist().date()


def week_start(d: date, boundary: Literal["monday", "sunday"] = "monday") -> date:
    """Return the start date of the week containing `d`.

    boundary='monday' → Monday is day 0
    boundary='sunday' → Sunday is day 0
    """
    weekday = d.weekday()  # Mon=0..Sun=6
    if boundary == "sunday":
        # shift so Sun=0, Mon=1, ..., Sat=6
        weekday = (weekday + 1) % 7
    return d - timedelta(days=weekday)


def week_of(d: Optional[date] = None, boundary: Literal["monday", "sunday"] = "monday") -> date:
    """Convenience: 'week of' for a given date (default today IST)."""
    return week_start(d or today_ist(), boundary=boundary)


def previous_week_of(d: date, boundary: Literal["monday", "sunday"] = "monday") -> date:
    """The week_of for the week immediately before the one containing d."""
    return week_start(d - timedelta(days=7), boundary=boundary)


def parse_cutoff(cutoff_str: str) -> tuple[int, time]:
    """Parse a 'Mon 13:00' style cutoff into (weekday_int, time_obj).

    weekday_int: Mon=0..Sun=6
    """
    days = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    parts = cutoff_str.strip().split()
    if len(parts) != 2:
        raise ValueError(f"Invalid cutoff format: {cutoff_str!r} (expected 'Mon 13:00')")
    day_part, time_part = parts
    if day_part not in days:
        raise ValueError(f"Invalid weekday in cutoff: {day_part!r}")
    h, m = time_part.split(":")
    return days[day_part], time(int(h), int(m))


def cutoff_datetime_for_week(week_of_date: date, cutoff_str: str) -> datetime:
    """Compute the absolute IST datetime of the cutoff for a given week.

    Cutoff is interpreted relative to week start: 'Mon 13:00' on a Monday-start
    week = the Monday of that week at 13:00 IST.
    """
    weekday, t = parse_cutoff(cutoff_str)
    cutoff_date = week_of_date + timedelta(days=weekday)
    return IST.localize(datetime.combine(cutoff_date, t))


def is_holiday_week(week_of_date: date, holiday_dates: set[date]) -> bool:
    """True if any of the 7 days in the given week is a holiday."""
    for i in range(7):
        if (week_of_date + timedelta(days=i)) in holiday_dates:
            return True
    return False
