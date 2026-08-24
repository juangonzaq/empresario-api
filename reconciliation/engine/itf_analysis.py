"""ITF as a *contrast* indicator — explicitly not a sales figure.

Credits reported by banks (operation codes 12/13) are compared against issued
sales; the output always carries the caveat that own transfers, loans,
contributions and prior-period collections inflate the movement side.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from sunat_itf.models import ItfRecord, ItfSection

from ..models import MatchLevel

CREDIT_CODES = {"12", "13"}
DEBIT_CODES = {"14", "15"}
RATIO_WARNING = Decimal(os.getenv("RECON_ITF_RATIO_WARNING", "2.0"))
RATIO_REVIEW = Decimal(os.getenv("RECON_ITF_RATIO_REVIEW", "4.0"))

CAVEAT = (
    "Esto no implica ventas omitidas: transferencias entre cuentas propias, préstamos, "
    "aportes y cobranzas de periodos anteriores también mueven las cuentas. Requiere revisión."
)


def _sum(account_ruc: str, period: str, codes: set[str]) -> Decimal:
    qs = ItfRecord.objects.for_taxpayer(account_ruc).for_period(period).in_section(ItfSection.ACCUMULATED)
    return sum((r.base_amount or Decimal("0") for r in qs if r.operation_code in codes), Decimal("0"))


def analyze(account_ruc: str, period: str, previous_period: str | None, sales_total: Decimal) -> dict[str, Any]:
    credits = _sum(account_ruc, period, CREDIT_CODES)
    debits = _sum(account_ruc, period, DEBIT_CODES)
    prev_credits = _sum(account_ruc, previous_period, CREDIT_CODES) if previous_period else None

    findings: list[dict[str, Any]] = []
    ratio = None
    if sales_total > 0 and credits > 0:
        ratio = credits / sales_total
        if ratio >= RATIO_REVIEW:
            findings.append({
                "kind": "ITF_OUT_OF_PROPORTION", "level": MatchLevel.REVIEW, "amount": float(credits - sales_total),
                "message": (f"Los abonos reportados por bancos (S/ {credits:,.2f}) equivalen a "
                            f"{ratio:.1f}× las ventas emitidas del periodo (S/ {sales_total:,.2f}). {CAVEAT}"),
            })
        elif ratio >= RATIO_WARNING:
            findings.append({
                "kind": "ITF_ABOVE_SALES", "level": MatchLevel.WARNING, "amount": float(credits - sales_total),
                "message": (f"Los abonos reportados (S/ {credits:,.2f}) superan las ventas emitidas "
                            f"(S/ {sales_total:,.2f}) en el periodo. {CAVEAT}"),
            })
    variation_pct = None
    if prev_credits and prev_credits > 0:
        variation_pct = float((credits - prev_credits) / prev_credits * 100)
    return {
        "credits": credits, "debits": debits,
        "ratio_credits_to_sales": float(ratio) if ratio is not None else None,
        "difference_credits_minus_sales": float(credits - sales_total),
        "variation_vs_previous_pct": variation_pct,
        "findings": findings,
    }
