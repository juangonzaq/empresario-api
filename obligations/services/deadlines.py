"""Due-date derived states, computed — never stored.

OVERDUE / DUE_SOON / UPCOMING are a function of ``due_date`` and today. Storing
them turns into stale data the moment a day passes; here they are recomputed on
every read.
"""

from __future__ import annotations

import datetime

DUE_SOON_DAYS = 14


def time_state(due_date: datetime.date | None, today: datetime.date) -> str | None:
    """One of ``overdue`` / ``due_soon`` / ``upcoming`` / ``None`` (no date)."""
    if due_date is None:
        return None
    delta = (due_date - today).days
    if delta < 0:
        return "overdue"
    if delta <= DUE_SOON_DAYS:
        return "due_soon"
    return "upcoming"


def days_until(due_date: datetime.date | None, today: datetime.date) -> int | None:
    if due_date is None:
        return None
    return (due_date - today).days
