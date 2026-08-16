"""Shared helpers: periods, configurable thresholds and currency-safe math.

Every aggregate in this app is grouped by currency — PEN and USD are never
added together without conversion, so totals are dicts keyed by currency.
"""

from __future__ import annotations

import os
from decimal import Decimal

from .xml_extract import fix_mojibake

MONTH_LABELS = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# Configurable thresholds (override via environment without code changes).
THRESHOLDS = {
    # % of net PEN sales concentrated in one customer.
    "concentration_pct": _env_number("FINANCE_CONCENTRATION_PCT", 30),
    "concentration_critical_pct": _env_number("FINANCE_CONCENTRATION_CRITICAL_PCT", 50),
    # Credit notes as % of gross issued sales in a period.
    "credit_note_ratio_pct": _env_number("FINANCE_CREDIT_NOTE_RATIO_PCT", 8),
    # Consecutive falling months that count as a sustained drop.
    "drop_streak_months": int(_env_number("FINANCE_DROP_STREAK_MONTHS", 3)),
    # Month-over-month variation considered relevant (ITF, purchases).
    "variation_alert_pct": _env_number("FINANCE_VARIATION_ALERT_PCT", 40),
    # New supplier whose first-month purchases exceed this PEN amount.
    "new_supplier_amount_pen": _env_number("FINANCE_NEW_SUPPLIER_AMOUNT_PEN", 10000),
    # Historic share that makes a stopped customer worth an alert.
    "lost_client_share_pct": _env_number("FINANCE_LOST_CLIENT_SHARE_PCT", 10),
    # Months without invoicing after which a customer counts as stopped.
    "lost_client_gap_months": int(_env_number("FINANCE_LOST_CLIENT_GAP_MONTHS", 3)),
    # Semáforo de gastos sobre ingresos: hasta dónde llega el verde y hasta
    # dónde el amarillo (en % de los ingresos netos PEN del mes); más allá es
    # rojo. Los de personal siguen la regla práctica 25-28 / 30-35 / 40.
    "semaforo_personal_verde_pct": _env_number("FINANCE_SEMAFORO_PERSONAL_VERDE_PCT", 28),
    "semaforo_personal_amarillo_pct": _env_number("FINANCE_SEMAFORO_PERSONAL_AMARILLO_PCT", 35),
    "semaforo_otros_verde_pct": _env_number("FINANCE_SEMAFORO_OTROS_VERDE_PCT", 40),
    "semaforo_otros_amarillo_pct": _env_number("FINANCE_SEMAFORO_OTROS_AMARILLO_PCT", 55),
    "semaforo_total_verde_pct": _env_number("FINANCE_SEMAFORO_TOTAL_VERDE_PCT", 70),
    "semaforo_total_amarillo_pct": _env_number("FINANCE_SEMAFORO_TOTAL_AMARILLO_PCT", 85),
    # Aporte EsSalud a cargo del empleador: 9 % de la remuneración, con base
    # mínima la RMV vigente. Configurables porque cambian por decreto.
    "essalud_pct": _env_number("FINANCE_ESSALUD_PCT", 9),
    "rmv_pen": _env_number("FINANCE_RMV_PEN", 1130),
}


def period_label(period: str) -> str:
    """'202606' → 'jun 2026'."""
    if len(period) != 6:
        return period
    try:
        return f"{MONTH_LABELS[int(period[4:]) - 1]} {period[:4]}"
    except (ValueError, IndexError):
        return period


def window_label(start: str, end: str) -> str:
    """'202508','202607' → 'ago 2025 – jul 2026'. Used to state, on every
    aggregate, exactly which months it was computed over."""
    if not start or not end:
        return ""
    return period_label(start) if start == end else f"{period_label(start)} – {period_label(end)}"


def previous_period(period: str) -> str:
    year, month = int(period[:4]), int(period[4:])
    if month == 1:
        return f"{year - 1}12"
    return f"{year}{month - 1:02d}"


def period_range_desc(latest: str, months: int) -> list[str]:
    """The ``months`` periods ending at ``latest``, ascending."""
    out = [latest]
    for _ in range(months - 1):
        out.append(previous_period(out[-1]))
    return list(reversed(out))


def pct_change(current: Decimal | float | None, previous: Decimal | float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((float(current) - float(previous)) / abs(float(previous)) * 100, 1)


def money(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def clean_name(name: str) -> str:
    return fix_mojibake((name or "").strip())
