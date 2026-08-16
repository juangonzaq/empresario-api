"""Fifth-category income tax: annual projection and monthly schedule (§5).

The projection mixes three sources: actual taxable income of CLOSED months,
the current month's computed base, and a projection of the future months
built from the current salary plus fixed recurring concepts — variable
overtime is deliberately NOT projected (§5.1).

The schedule redistributes only the pending balance across open months;
settled months are immutable (§5.4). That single rule is what removes the
December surprise from manual payrolls.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from colaboradores.models import Colaborador

from ..models import (
    IncomeTaxProjection, IncomeTaxWithholdingSchedule, PayrollEntry,
    PayrollStatus,
)
from .master_data import RatesSnapshot
from .money import ZERO, D, money

# Months when statutory bonuses (gratificaciones) are paid, and the first
# month of the semester each one computes over.
STATUTORY_BONUS_MONTHS = {7: 1, 12: 7}


class BracketMismatch(Exception):
    """V8: the bracket split does not add up to the taxable income. The
    scale is mis-loaded; abort instead of emitting a silently wrong tax."""


def annual_tax(
    taxable_income: Decimal, tax_unit: Decimal, brackets,
) -> tuple[Decimal, list[dict]]:
    """Progressive scale over brackets stored in UIT (§5.3)."""
    remaining, tax, detail = taxable_income, Decimal("0"), []
    for bracket in brackets:
        width = (
            D(bracket.width_in_tax_units) * tax_unit
            if bracket.width_in_tax_units is not None else remaining
        )
        amount = min(remaining, width)
        slice_tax = amount * D(bracket.rate)
        detail.append({
            "order": bracket.order,
            "width": str(width),
            "amount": str(amount),
            "rate": str(bracket.rate),
            "tax": str(money(slice_tax)),
        })
        tax += slice_tax
        remaining -= amount
        if remaining <= 0:
            break
    covered = sum(Decimal(d["amount"]) for d in detail)
    if covered != taxable_income:
        raise BracketMismatch(
            f"tramos cubren {covered}, renta imponible {taxable_income}"
        )
    return money(tax), detail


def _monthly_recurring_income(
    colaborador: Colaborador, rates: RatesSnapshot,
) -> Decimal:
    """Current salary plus fixed recurring concepts (family allowance)."""
    salary = D(colaborador.monthly_salary or 0)
    if colaborador.receives_family_allowance:
        salary += rates.minimum_wage * D(rates.settings.family_allowance_rate)
    return salary


def _projected_bonuses(
    colaborador: Colaborador, rates: RatesSnapshot, current_month: int,
) -> Decimal:
    """Future statutory bonuses of the year, prorated by service months in
    each semester. The current month's bonus is not projected: it enters
    the period as its own STATUTORY_BONUS line and counts as actual."""
    computable = _monthly_recurring_income(colaborador, rates)
    hired = colaborador.hired_on
    total = Decimal("0")
    for pay_month, semester_start in STATUTORY_BONUS_MONTHS.items():
        if pay_month <= current_month:
            continue
        months_in_semester = 0
        for m in range(semester_start, semester_start + 6):
            month_start = datetime.date(rates.year, m, 1)
            if hired is not None and hired > month_start:
                continue
            if (
                colaborador.terminated_on is not None
                and colaborador.terminated_on < month_start
            ):
                continue
            months_in_semester += 1
        total += computable * Decimal(months_in_semester) / Decimal(6)
    return total


def recalculate_projection(
    colaborador: Colaborador,
    rates: RatesSnapshot,
    current_month_taxable: Decimal,
) -> IncomeTaxProjection:
    """Rebuild the projection and redistribute the schedule (§5.1–§5.4).

    Idempotent on purpose: it can run on every cell edit, every close and
    every salary change without drifting.
    """
    year, month = rates.year, rates.month

    # Actual taxable income from CLOSED months of the year (§5.1): closed
    # months contribute what was really paid, never an estimate.
    closed_actual = (
        PayrollEntry.objects.filter(
            colaborador=colaborador,
            period__year=year,
            period__status=PayrollStatus.CLOSED,
            period__month__lt=month,
        ).aggregate(total=Sum("income_tax_base"))["total"] or ZERO
    )

    recurring = _monthly_recurring_income(colaborador, rates)
    future_months = max(rates.tax_settings.months_in_projection - month, 0)
    projected = (
        D(closed_actual)
        + current_month_taxable
        + recurring * Decimal(future_months)
        + _projected_bonuses(colaborador, rates, month)
    )

    projection, _ = IncomeTaxProjection.objects.get_or_create(
        colaborador=colaborador, year=year
    )
    total_income = (
        projected
        + D(projection.previous_employer_income)
        + D(projection.profit_sharing)
    )

    deduction = rates.tax_unit * D(
        rates.tax_settings.standard_deduction_in_tax_units
    )
    taxable = money(max(total_income - deduction, Decimal("0")))

    if taxable > 0:
        tax, detail = annual_tax(taxable, rates.tax_unit, rates.brackets)
    else:
        # No negative withholding, ever (§5.2).
        tax, detail = ZERO, []

    projection.projected_annual_income = money(projected)
    projection.taxable_income = taxable
    projection.annual_tax = tax
    projection.bracket_detail = detail
    projection.recalculated_at = timezone.now()
    projection.save()

    _redistribute_schedule(projection, month, rates)
    return projection


def _redistribute_schedule(
    projection: IncomeTaxProjection, current_month: int, rates: RatesSnapshot,
) -> None:
    """Spread the pending balance over the open months (§5.4). Settled
    months are never touched — that is the invariant."""
    months = rates.tax_settings.months_in_projection
    existing = {row.month: row for row in projection.schedule.all()}

    already_withheld = sum(
        (D(row.amount) for row in existing.values() if row.is_settled), ZERO
    )
    pending = (
        D(projection.annual_tax)
        - already_withheld
        - D(projection.previous_employer_withheld)
    )
    open_months = [
        m for m in range(current_month, months + 1)
        if not (m in existing and existing[m].is_settled)
    ]
    amounts: dict[int, Decimal] = {}
    if open_months and pending > 0:
        monthly = money(pending / Decimal(len(open_months)))
        for m in open_months:
            amounts[m] = monthly
        # The rounding remainder lands on the LAST open month, so the
        # year-end conciliation (V9) closes to the exact cent.
        amounts[open_months[-1]] = money(
            pending - monthly * (len(open_months) - 1)
        )

    for m in range(1, months + 1):
        row = existing.get(m)
        if row is not None and row.is_settled:
            continue
        if m in open_months:
            amount = amounts.get(m, ZERO)
        elif row is not None and m < current_month:
            amount = row.amount
        else:
            amount = ZERO
        if row is None:
            IncomeTaxWithholdingSchedule.objects.create(
                projection=projection, month=m, amount=amount
            )
        elif row.amount != amount:
            row.amount = amount
            row.save(update_fields=["amount", "updated_at"])


def scheduled_amount(colaborador: Colaborador, year: int, month: int) -> Decimal:
    """What §4.7 reads: the schedule value, never a recalculation."""
    row = IncomeTaxWithholdingSchedule.objects.filter(
        projection__colaborador=colaborador, projection__year=year, month=month
    ).first()
    return D(row.amount) if row else ZERO


def settle_month(colaborador: Colaborador, year: int, month: int) -> None:
    """Mark a schedule month as settled when its period closes."""
    IncomeTaxWithholdingSchedule.objects.filter(
        projection__colaborador=colaborador, projection__year=year, month=month
    ).update(is_settled=True)
