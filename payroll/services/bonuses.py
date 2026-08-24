"""Statutory bonus (gratificación legal) math, shared by the monthly
calculator and the annual income-tax projection.

July and December each pay one computable salary, prorated by the months
of the semester actually worked (Ley 27735). The extraordinary bonus that
accompanies it (Ley 30334) is 9 % of the bonus — or the EPS rate when the
worker is affiliated to one — which is why the rate lives in
``SocialHealthRate`` and never in a constant here.

Keeping this in one module is what guarantees the payslip and the tax
projection can never disagree about what a bonus is worth.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from colaboradores.models import Colaborador

from .master_data import RatesSnapshot
from .money import D

# Month the bonus is paid → first month of the semester it computes over.
STATUTORY_BONUS_MONTHS = {7: 1, 12: 7}


def monthly_recurring_income(
    colaborador: Colaborador, rates: RatesSnapshot,
) -> Decimal:
    """Current salary plus fixed recurring concepts (family allowance).
    This is both the future-month projection base and the bonus computable
    base — variable overtime is deliberately excluded from both."""
    salary = D(colaborador.monthly_salary or 0)
    if colaborador.receives_family_allowance:
        salary += rates.minimum_wage * D(rates.settings.family_allowance_rate)
    return salary


def semester_months_worked(
    colaborador: Colaborador, year: int, pay_month: int,
) -> int:
    """Complete months of the bonus semester the worker was employed."""
    semester_start = STATUTORY_BONUS_MONTHS[pay_month]
    hired = colaborador.hired_on
    months = 0
    for m in range(semester_start, semester_start + 6):
        month_start = datetime.date(year, m, 1)
        if hired is not None and hired > month_start:
            continue
        if (
            colaborador.terminated_on is not None
            and colaborador.terminated_on < month_start
        ):
            continue
        months += 1
    return months


def statutory_bonus_amount(
    colaborador: Colaborador, rates: RatesSnapshot, pay_month: int,
) -> Decimal:
    """The legal bonus for one pay month: computable salary prorated by
    the semester months actually worked."""
    computable = monthly_recurring_income(colaborador, rates)
    worked = semester_months_worked(colaborador, rates.year, pay_month)
    return computable * Decimal(worked) / Decimal(6)


def extraordinary_bonus_rate(
    colaborador: Colaborador, rates: RatesSnapshot,
) -> Decimal:
    """Rate of the extraordinary bonus that rides on the statutory one."""
    health = rates.social_health
    rate = (
        health.eps_rate if colaborador.has_eps
        else health.extraordinary_bonus_rate
    )
    return D(rate)
