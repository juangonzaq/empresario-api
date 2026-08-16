"""Attendance arithmetic shared by the runner and the validations.

Lives apart so ``validations`` never imports ``runner`` (which imports
``validations`` back).
"""

from __future__ import annotations

from colaboradores.models import Colaborador

from .master_data import period_bounds


def link_days(
    colaborador: Colaborador, year: int, month: int, days_per_month: int
) -> int:
    """Commercial-month days the employment link covers inside the period:
    the cap for attendance (V1) and the seed for default worked days. A
    full month is always ``days_per_month`` (30), whatever the calendar
    says; partial months use real calendar days, capped."""
    start, end = period_bounds(year, month)
    hired = colaborador.hired_on or start
    finished = colaborador.terminated_on or end
    first = max(start, hired)
    last = min(end, finished)
    if first > last:
        return 0
    if first == start and last == end:
        return days_per_month
    real = (last - first).days + 1
    return min(real, days_per_month)
