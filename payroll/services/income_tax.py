"""Fifth-category income tax: annual projection and monthly schedule (§5).

The projection covers the FULL twelve months of the year, always:

- Months already run and CLOSED by the engine contribute their real
  taxable base.
- Months the engine never ran (a mid-year start) contribute the actual
  amounts the accountant loaded month by month — without that history the
  projection would see half the income and withhold far too little.
- The current month contributes its computed base, statutory bonus
  included when it is a bonus month.
- Future months contribute the current salary plus fixed recurring
  concepts, and the pending statutory bonuses WITH their extraordinary
  bonus — variable overtime is deliberately NOT projected (§5.1).

The schedule redistributes only the pending balance across open months
(convention: balance ÷ remaining open months, re-run on every change —
the reglamento's staggered divisors converge to the same yearly total).
Settled months are immutable (§5.4), and a month pinned by an audited
override is treated as fixed: only the rest redistributes.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from colaboradores.models import Colaborador

from ..models import (
    IncomeTaxMonthlyInput, IncomeTaxProjection, IncomeTaxWithholdingSchedule,
    PayrollEntry, PayrollStatus,
)
from . import bonuses
from .master_data import RatesSnapshot
from .money import ZERO, D, money


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


def _actual_months(
    colaborador: Colaborador, year: int, current_month: int,
) -> dict[int, dict]:
    """Real taxable income and withholding for every month before the
    current one. An engine-closed month always wins over a loaded input:
    the engine's record is the payslip that was actually emitted."""
    months: dict[int, dict] = {}
    inputs = IncomeTaxMonthlyInput.objects.filter(
        colaborador=colaborador, year=year, month__lt=current_month,
    )
    for row in inputs:
        months[row.month] = {
            "taxable": D(row.taxable_income),
            "withheld": D(row.withheld),
            "source": "loaded",
        }
    closed = PayrollEntry.objects.filter(
        colaborador=colaborador,
        period__year=year,
        period__status=PayrollStatus.CLOSED,
        period__month__lt=current_month,
    ).select_related("period")
    for entry in closed:
        months[entry.period.month] = {
            "taxable": D(entry.income_tax_base),
            "withheld": D(entry.income_tax_withholding),
            "source": "engine",
        }
    return months


def _projected_bonuses(
    colaborador: Colaborador, rates: RatesSnapshot, current_month: int,
) -> dict[int, Decimal]:
    """Future statutory bonuses of the year WITH their extraordinary
    bonus — both are fifth-category income (V3) — keyed by pay month so
    the annual view can place each one in its column. The current
    month's bonus is not projected here: it enters the period as its own
    line and arrives through the month's computed base."""
    rate = bonuses.extraordinary_bonus_rate(colaborador, rates)
    by_month: dict[int, Decimal] = {}
    for pay_month in bonuses.STATUTORY_BONUS_MONTHS:
        if pay_month <= current_month:
            continue
        amount = bonuses.statutory_bonus_amount(colaborador, rates, pay_month)
        if amount > 0:
            by_month[pay_month] = amount * (1 + rate)
    return by_month


def _monthly_income_map(
    actual: dict[int, dict],
    current_month: int,
    months_in_year: int,
    current_month_taxable: Decimal,
    recurring: Decimal,
    bonus_by_month: dict[int, Decimal],
) -> dict[int, Decimal]:
    """One income figure per month of the year — the row the accountant
    reads in her spreadsheet: real for the past, computed for the current
    month, projected (bonus included) for the future."""
    months: dict[int, Decimal] = {}
    for m in range(1, months_in_year + 1):
        if m < current_month:
            months[m] = actual[m]["taxable"] if m in actual else Decimal("0")
        elif m == current_month:
            months[m] = current_month_taxable
        else:
            months[m] = recurring + bonus_by_month.get(m, Decimal("0"))
    return months


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

    actual = _actual_months(colaborador, year, month)
    actual_income = sum((row["taxable"] for row in actual.values()), ZERO)

    recurring = bonuses.monthly_recurring_income(colaborador, rates)
    months_in_year = rates.tax_settings.months_in_projection
    future_months = max(months_in_year - month, 0)
    bonus_by_month = _projected_bonuses(colaborador, rates, month)
    projected_bonuses = sum(bonus_by_month.values(), Decimal("0"))
    projected = (
        actual_income
        + current_month_taxable
        + recurring * Decimal(future_months)
        + projected_bonuses
    )

    monthly_income = _monthly_income_map(
        actual, month, months_in_year, current_month_taxable,
        recurring, bonus_by_month,
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

    schedule_info = _redistribute_schedule(projection, actual, month, rates)
    projection.computation_detail = {
        "current_month": month,
        "actual_months": {
            str(m): {
                "taxable": str(money(row["taxable"])),
                "withheld": str(money(row["withheld"])),
                "source": row["source"],
            }
            for m, row in sorted(actual.items())
        },
        "actual_income": str(money(actual_income)),
        "current_month_taxable": str(money(current_month_taxable)),
        "future_monthly_income": str(money(recurring)),
        "future_months": future_months,
        "projected_bonuses": str(money(projected_bonuses)),
        "monthly_income": {
            str(m): str(money(v)) for m, v in monthly_income.items()
        },
        "previous_employer_income": str(projection.previous_employer_income),
        "profit_sharing": str(projection.profit_sharing),
        "standard_deduction": str(money(deduction)),
        **schedule_info,
    }
    projection.recalculated_at = timezone.now()
    projection.save()
    return projection


def _redistribute_schedule(
    projection: IncomeTaxProjection,
    actual: dict[int, dict],
    current_month: int,
    rates: RatesSnapshot,
) -> dict:
    """Spread the pending balance over the open months (§5.4).

    Immutable months, in precedence order: settled (their period closed),
    loaded (the accountant recorded what was really withheld before the
    system existed), and overridden open months (pinned by an audited
    adjustment). Only what remains after all of them redistributes.
    """
    months = rates.tax_settings.months_in_projection
    existing = {row.month: row for row in projection.schedule.all()}

    settled_withheld = sum(
        (D(row.amount) for row in existing.values() if row.is_settled), ZERO
    )
    # Loaded months count as already practiced unless the schedule settled
    # them (then the engine's own record already covered the month).
    loaded_months = {
        m: row for m, row in actual.items()
        if row["source"] == "loaded"
        and not (m in existing and existing[m].is_settled)
    }
    loaded_withheld = sum(
        (row["withheld"] for row in loaded_months.values()), ZERO
    )

    overridden = {
        m: existing[m] for m in range(current_month, months + 1)
        if m in existing
        and not existing[m].is_settled
        and existing[m].override_amount is not None
    }
    overridden_total = sum(
        (D(row.override_amount) for row in overridden.values()), ZERO
    )

    pending = (
        D(projection.annual_tax)
        - settled_withheld
        - loaded_withheld
        - D(projection.previous_employer_withheld)
        - overridden_total
    )
    open_months = [
        m for m in range(current_month, months + 1)
        if not (m in existing and existing[m].is_settled)
        and m not in overridden
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

    def target_amount(m: int, row) -> Decimal:
        if m in open_months:
            return amounts.get(m, ZERO)
        if m in loaded_months:
            # Mirror the loaded withholding so the year reads complete.
            return money(loaded_months[m]["withheld"])
        if row is not None and m < current_month:
            return row.amount
        return ZERO

    frozen = set(overridden)
    _write_schedule_rows(projection, existing, months, frozen, target_amount)

    return {
        "already_withheld": str(money(settled_withheld + loaded_withheld)),
        "previous_employer_withheld": str(
            projection.previous_employer_withheld
        ),
        "overridden_total": str(money(overridden_total)),
        "pending": str(money(max(pending, ZERO))),
        "open_months": open_months,
        # V17: loaded withholdings above the annual tax leave a zero
        # balance, never a negative one — but the accountant must see it.
        "over_withheld": pending < 0,
    }


def _write_schedule_rows(
    projection: IncomeTaxProjection,
    existing: dict[int, IncomeTaxWithholdingSchedule],
    months: int,
    frozen: set[int],
    target_amount,
) -> None:
    """Persist the redistribution. Settled and frozen (overridden) rows
    are never written — immutability enforced at the write site too."""
    for m in range(1, months + 1):
        row = existing.get(m)
        if row is not None and (row.is_settled or m in frozen):
            continue
        amount = target_amount(m, row)
        if row is None:
            IncomeTaxWithholdingSchedule.objects.create(
                projection=projection, month=m, amount=amount
            )
        elif row.amount != amount:
            row.amount = amount
            row.save(update_fields=["amount", "updated_at"])


def scheduled_amount(colaborador: Colaborador, year: int, month: int) -> Decimal:
    """What §4.7 reads: the schedule value — the audited override when one
    exists — never a recalculation."""
    row = IncomeTaxWithholdingSchedule.objects.filter(
        projection__colaborador=colaborador, projection__year=year, month=month
    ).first()
    return D(row.effective_amount) if row else ZERO


def settle_month(
    colaborador: Colaborador, year: int, month: int, amount: Decimal,
) -> None:
    """Mark a schedule month as settled when its period closes, and
    reconcile it to what the payslip actually withheld: the schedule is a
    plan until the close, then it becomes the record (V9)."""
    row = IncomeTaxWithholdingSchedule.objects.filter(
        projection__colaborador=colaborador, projection__year=year, month=month
    ).first()
    if row is None:
        return  # no projection: nothing was ever scheduled for this person
    row.amount = money(amount)
    row.is_settled = True
    row.save(update_fields=["amount", "is_settled", "updated_at"])
