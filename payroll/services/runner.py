"""Payroll run orchestration (spec §6) and the period state machine.

Step 5 (income-tax projection) does not create a circular dependency: the
projection feeds from the contractual salary and fixed concepts, plus the
month's already-computed base — never from its own withholding — so one
pass per employee is enough.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from colaboradores.models import Colaborador

from ..models import (
    ConceptKind, PayrollEntry, PayrollEntryLine, PayrollPeriod, PayrollStatus,
)
from . import income_tax, validations
from .calculator import Attendance, Line, MissingSalaryError, PayrollCalculator
from .master_data import RatesSnapshot, period_bounds
from .money import ZERO, D, money
from .runner_support import link_days as link_days_in_period


class PeriodLocked(Exception):
    """V15: no writes on a CLOSED period, ever."""


class TransitionError(Exception):
    """A state change that the machine does not allow."""


# ------------------------------------------------------------------ staff
def eligible_employees(taxpayer_id: str, year: int, month: int):
    """§6.2: active employees, or those whose termination falls inside the
    period, hired no later than its end."""
    start, end = period_bounds(year, month)
    return (
        Colaborador.objects.filter(taxpayer_id=taxpayer_id)
        .filter(
            Q(is_active=True)
            | Q(terminated_on__gte=start, terminated_on__lte=end)
        )
        .exclude(hired_on__gt=end)
        .order_by("full_name")
    )




# ------------------------------------------------------------------ setup
@transaction.atomic
def create_period(taxpayer_id: str, year: int, month: int) -> PayrollPeriod:
    """Create the period with pre-seeded entries (§7.5: the user should
    only correct exceptions, not type a month from scratch)."""
    rates = RatesSnapshot.resolve(taxpayer_id, year, month)  # V16 up front
    period = PayrollPeriod.objects.create(
        taxpayer_id=taxpayer_id, year=year, month=month,
        tax_unit_amount=rates.tax_unit,
        minimum_wage_amount=rates.minimum_wage,
        settings_snapshot=rates.as_snapshot(),
    )
    for colaborador in eligible_employees(taxpayer_id, year, month):
        entry = PayrollEntry.objects.create(
            period=period,
            colaborador=colaborador,
            worked_days=link_days_in_period(
                colaborador, year, month, rates.settings.days_per_month
            ),
        )
        _snapshot_employee(entry, colaborador)
        if colaborador.monthly_salary is not None:
            _calculate_entry(entry, rates)
    return period


def _snapshot_employee(entry: PayrollEntry, colaborador: Colaborador) -> None:
    entry.base_salary = colaborador.monthly_salary
    entry.pension_regime = colaborador.regimen
    entry.pension_fund = colaborador.afp
    entry.commission_type = colaborador.pension_commission_type
    entry.cuspp = colaborador.cuspp


# ------------------------------------------------------------ calculation
def _manual_lines(entry: PayrollEntry, rates: RatesSnapshot) -> list[Line]:
    """Manual concepts survive recalculation; computed lines are rebuilt."""
    kept = []
    for line in entry.lines.filter(is_manual=True).select_related("concept"):
        kept.append(Line(
            concept=rates.concept(line.concept.code),
            amount=D(line.amount),
            quantity=line.quantity,
            is_manual=True,
        ))
    return kept


def _calculate_entry(entry: PayrollEntry, rates: RatesSnapshot) -> PayrollEntry:
    colaborador = entry.colaborador
    _snapshot_employee(entry, colaborador)

    attendance = Attendance(
        worked_days=entry.worked_days,
        vacation_days=entry.vacation_days,
        sick_leave_days=entry.sick_leave_days,
        leave_days=entry.leave_days,
        absence_days=entry.absence_days,
        first_overtime_hours=D(entry.first_overtime_hours),
        additional_overtime_hours=D(entry.additional_overtime_hours),
    )
    manual = _manual_lines(entry, rates)
    calculator = PayrollCalculator(rates)

    # First pass without withholding to know the month's taxable base,
    # then the projection, then the final pass reading the schedule (§4.7).
    draft = calculator.calculate(colaborador, attendance, manual, ZERO)
    income_tax.recalculate_projection(colaborador, rates, draft.income_tax_base)
    tax = income_tax.scheduled_amount(colaborador, rates.year, rates.month)
    result = calculator.calculate(colaborador, attendance, manual, tax)

    entry.pension_base = result.pension_base
    entry.income_tax_base = result.income_tax_base
    entry.gross_pay = result.gross_pay
    for field, value in result.totals.items():
        setattr(entry, field, value)
    entry.total_hours = money(
        Decimal(entry.worked_days) * rates.settings.hours_per_day
        + D(entry.first_overtime_hours) + D(entry.additional_overtime_hours)
    )
    entry.rates_snapshot = result.rates_snapshot
    entry.save()

    entry.lines.all().delete()
    PayrollEntryLine.objects.bulk_create([
        PayrollEntryLine(
            entry=entry, concept=line.concept, amount=line.amount,
            quantity=line.quantity, is_manual=line.is_manual,
        )
        for line in result.lines
    ])
    return entry


@transaction.atomic
def recalculate_entry(entry: PayrollEntry) -> PayrollEntry:
    """Live recalculation after a cell edit (§7.1)."""
    period = entry.period
    if not period.is_editable:
        raise PeriodLocked(period.status)
    rates = RatesSnapshot.resolve(period.taxpayer_id, period.year, period.month)
    if entry.colaborador.monthly_salary is None:
        _snapshot_employee(entry, entry.colaborador)
        entry.save()
        return entry
    return _calculate_entry(entry, rates)


@transaction.atomic
def calculate_period(period: PayrollPeriod) -> PayrollPeriod:
    """Full run (§6): recalculate every entry, refresh snapshots, and move
    to CALCULATED. Restartable at will while the period stays open."""
    if period.is_closed:
        raise PeriodLocked(period.status)
    rates = RatesSnapshot.resolve(period.taxpayer_id, period.year, period.month)
    period.tax_unit_amount = rates.tax_unit
    period.minimum_wage_amount = rates.minimum_wage
    period.settings_snapshot = rates.as_snapshot()

    known = {entry.colaborador_id for entry in period.entries.all()}
    for colaborador in eligible_employees(
        period.taxpayer_id, period.year, period.month
    ):
        if colaborador.pk in known:
            continue
        PayrollEntry.objects.create(
            period=period, colaborador=colaborador,
            worked_days=link_days_in_period(
                colaborador, period.year, period.month,
                rates.settings.days_per_month,
            ),
        )

    for entry in period.entries.select_related("colaborador"):
        if entry.colaborador.monthly_salary is None:
            _snapshot_employee(entry, entry.colaborador)  # V5: excluded
            entry.save()
            continue
        _calculate_entry(entry, rates)

    incidents = validations.validate_period(period)
    for entry in period.entries.all():
        flagged = any(
            i.entry_id == str(entry.pk) and i.severity == "warning"
            for i in incidents
        )
        if entry.has_warnings != flagged:
            entry.has_warnings = flagged
            entry.save(update_fields=["has_warnings", "updated_at"])

    period.status = PayrollStatus.CALCULATED
    period.calculated_at = timezone.now()
    period.save()
    return period


# ---------------------------------------------------------- state machine
def approve_period(period: PayrollPeriod) -> PayrollPeriod:
    if period.status != PayrollStatus.CALCULATED:
        raise TransitionError("Solo un periodo calculado puede aprobarse.")
    incidents = validations.validate_period(period)
    if any(i.severity == "error" for i in incidents):
        raise TransitionError("El periodo tiene errores pendientes.")
    period.status = PayrollStatus.APPROVED
    period.approved_at = timezone.now()
    period.save(update_fields=["status", "approved_at", "updated_at"])
    return period


def reopen_period(period: PayrollPeriod) -> PayrollPeriod:
    """APPROVED → CALCULATED. A closed period never reopens (V15)."""
    if period.status != PayrollStatus.APPROVED:
        raise TransitionError("Solo un periodo aprobado puede reabrirse.")
    period.status = PayrollStatus.CALCULATED
    period.approved_at = None
    period.save(update_fields=["status", "approved_at", "updated_at"])
    return period


@transaction.atomic
def close_period(
    period: PayrollPeriod, user, accept_warnings: bool = False,
) -> PayrollPeriod:
    """Irreversible close (§7.3): blocks on errors, demands an explicit
    confirmation for warnings, and settles the tax schedule months."""
    if period.status != PayrollStatus.APPROVED:
        raise TransitionError("Solo un periodo aprobado puede cerrarse.")
    incidents = validations.validate_period(period)
    if any(i.severity == "error" for i in incidents):
        raise TransitionError("El periodo tiene errores: no puede cerrarse.")
    warnings = [i for i in incidents if i.severity == "warning"]
    if warnings and not accept_warnings:
        raise TransitionError(
            "El periodo tiene advertencias: cerrar exige aceptarlas."
        )

    for entry in period.entries.all():
        income_tax.settle_month(entry.colaborador, period.year, period.month)

    period.status = PayrollStatus.CLOSED
    period.closed_at = timezone.now()
    period.closed_by = user if user is not None and user.is_authenticated else None
    if warnings:
        period.warnings_accepted_by = getattr(user, "email", "") or str(user)
    period.save()
    return period
