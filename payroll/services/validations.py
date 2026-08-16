"""Validation pack (spec §9). Errors block the close; warnings inform and
demand an explicit acceptance at close time. Every incident points at the
exact entry and field so the grid can jump to the cell (§7.2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal

from colaboradores.models import Contrato, EstadoContrato, RegimenPensionario

from ..models import PayrollEntry, PayrollPeriod, PayrollStatus
from .money import D
from .runner_support import link_days  # thin alias, see below

# Days a new hire has to choose a pension regime (V3). A validation
# threshold, not a monetary figure: it flags a warning, it never enters an
# amount, so it may live here without breaking spec rule §0.4.
REGIME_ELECTION_DAYS = 10


@dataclass
class Incident:
    code: str
    severity: str  # "error" | "warning"
    message: str
    entry_id: str | None = None
    colaborador_id: str | None = None
    colaborador_name: str = ""
    field: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _entry_incident(
    entry: PayrollEntry, code: str, severity: str, message: str,
    field: str | None = None,
) -> Incident:
    return Incident(
        code=code, severity=severity, message=message,
        entry_id=str(entry.pk),
        colaborador_id=str(entry.colaborador_id),
        colaborador_name=entry.colaborador.full_name,
        field=field,
    )


def validate_period(period: PayrollPeriod) -> list[Incident]:
    incidents: list[Incident] = []
    entries = list(
        period.entries.select_related("colaborador").order_by(
            "colaborador__full_name"
        )
    )
    previous = _previous_nets(period)
    expired_contracts = _expired_contract_ids(period)

    for entry in entries:
        incidents += _validate_entry(period, entry, previous, expired_contracts)
    return incidents


def _validate_entry(
    period: PayrollPeriod, entry: PayrollEntry,
    previous_nets: dict, expired_contracts: set,
) -> list[Incident]:
    out: list[Incident] = []
    colaborador = entry.colaborador
    settings = period.settings_snapshot.get("settings", {})
    days_per_month = int(settings.get("days_per_month", 30))

    # V5 — active employee without salary is excluded from the run.
    if colaborador.monthly_salary is None:
        out.append(_entry_incident(
            entry, "V5", "error",
            "Sin sueldo registrado: se excluye de la corrida hasta tener el dato.",
            field="base_salary",
        ))
        return out  # nothing else is meaningful without a salary

    # V11 — termination before hire is a data error.
    if (
        colaborador.terminated_on is not None
        and colaborador.hired_on is not None
        and colaborador.terminated_on < colaborador.hired_on
    ):
        out.append(_entry_incident(
            entry, "V11", "error",
            "La fecha de cese es anterior a la fecha de ingreso.",
        ))

    # V1 — day counters cannot exceed the link days within the period.
    cap = link_days(colaborador, period.year, period.month, days_per_month)
    total_days = (
        entry.worked_days + entry.vacation_days + entry.sick_leave_days
        + entry.leave_days + entry.absence_days
    )
    if total_days > cap:
        out.append(_entry_incident(
            entry, "V1", "error",
            f"La suma de días ({total_days}) excede los {cap} del vínculo "
            "en el periodo.",
            field="worked_days",
        ))

    # V12 — hire or termination inside the month with unprorated days.
    if cap < days_per_month and total_days == days_per_month:
        out.append(_entry_incident(
            entry, "V12", "warning",
            "Ingreso o cese dentro del mes con los días sin prorratear.",
            field="worked_days",
        ))

    # V2 — regime coherence.
    if colaborador.regimen == RegimenPensionario.AFP and (
        not colaborador.afp or not colaborador.pension_commission_type
    ):
        out.append(_entry_incident(
            entry, "V2", "error",
            "Régimen AFP exige administradora y tipo de comisión.",
            field="pension_fund",
        ))
    if colaborador.regimen == RegimenPensionario.ONP and (
        colaborador.afp or colaborador.pension_commission_type
    ):
        out.append(_entry_incident(
            entry, "V2", "error",
            "Régimen ONP no lleva AFP ni tipo de comisión.",
            field="pension_fund",
        ))

    # V3 — no regime past the legal election window.
    if (
        colaborador.regimen == RegimenPensionario.SIN_REGIMEN
        and colaborador.hired_on is not None
        and period.settings_snapshot
    ):
        from .master_data import period_bounds

        _, end = period_bounds(period.year, period.month)
        if end - colaborador.hired_on > timedelta(days=REGIME_ELECTION_DAYS):
            out.append(_entry_incident(
                entry, "V3", "warning",
                "Sin régimen pensionario pasado el plazo de elección: este "
                "mes no se le retuvo aporte.",
            ))

    # V4 — AFP without CUSPP.
    if colaborador.regimen == RegimenPensionario.AFP and not colaborador.cuspp:
        out.append(_entry_incident(
            entry, "V4", "warning",
            "Régimen AFP sin CUSPP: el archivo de pago a la AFP saldrá incompleto.",
        ))

    # V6 / V7 — the math must close.
    if entry.net_pay < 0:
        out.append(_entry_incident(
            entry, "V6", "error", "El neto a pagar es negativo.", field="net_pay",
        ))
    if entry.total_withholdings + entry.total_deductions > entry.gross_pay:
        out.append(_entry_incident(
            entry, "V7", "error",
            "Retenciones más descuentos superan el total de ingresos.",
        ))

    # V13 — paid while the contract is expired.
    if str(colaborador.pk) in expired_contracts:
        out.append(_entry_incident(
            entry, "V13", "warning",
            "Se le está pagando con contrato vencido.",
        ))

    # V14 — net variation against the previous period.
    threshold = D(settings.get("net_variation_alert_pct", "20"))
    before = previous_nets.get(entry.colaborador_id)
    if before and before > 0 and entry.net_pay > 0:
        variation = abs(D(entry.net_pay) - before) / before * 100
        if variation > threshold:
            out.append(_entry_incident(
                entry, "V14", "warning",
                f"El neto varía {variation.quantize(Decimal('0.1'))} % contra "
                "el mes anterior.",
                field="net_pay",
            ))

    return out


def _previous_nets(period: PayrollPeriod) -> dict:
    year, month = period.year, period.month - 1
    if month == 0:
        year, month = year - 1, 12
    previous = PayrollPeriod.objects.filter(
        taxpayer_id=period.taxpayer_id, year=year, month=month
    ).first()
    if previous is None:
        return {}
    return {
        entry.colaborador_id: D(entry.net_pay)
        for entry in previous.entries.all()
    }


def _expired_contract_ids(period: PayrollPeriod) -> set:
    expired = set()
    contracts = Contrato.objects.filter(
        taxpayer_id=period.taxpayer_id
    ).select_related("colaborador")
    latest: dict = {}
    for contract in contracts.order_by("fecha_inicio"):
        latest[str(contract.colaborador_id)] = contract
    for colaborador_id, contract in latest.items():
        if contract.estado == EstadoContrato.VENCIDO:
            expired.add(colaborador_id)
    return expired
