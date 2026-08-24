"""The 0–100 tax-consistency score.

Not a sum of absolute amounts: it weighs how many findings there are, how
severe, how much money is involved *relative to sales*, whether they repeat
across periods, and it forgives what a person already justified or corrected.
Every deduction lands in the breakdown so the number can be explained.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from finance_analytics.models import AlertStatus

from ..models import MatchLevel

PENALTY = {MatchLevel.WARNING: 3, MatchLevel.REVIEW: 8, MatchLevel.CRITICAL: 15}
FORGIVEN_STATUSES = {AlertStatus.JUSTIFIED, AlertStatus.CORRECTED, AlertStatus.DISMISSED, AlertStatus.RESOLVED}


def compute(
    findings: list[dict[str, Any]],
    alerts_by_kind: dict[str, Any],
    sales_total: Decimal,
    unclassified_credits: Decimal,
    recurrent_kinds: set[str],
) -> tuple[int, list[dict[str, Any]]]:
    score = 100
    breakdown: list[dict[str, Any]] = []

    def deduct(points: int, factor: str, detail: str) -> None:
        nonlocal score
        points = min(points, score)
        if points <= 0:
            return
        score -= points
        breakdown.append({"factor": factor, "penalty": points, "detail": detail})

    for f in findings:
        if f["level"] == MatchLevel.OK:
            continue
        alert = alerts_by_kind.get(f["kind"])
        if alert is not None and alert.status in FORGIVEN_STATUSES:
            breakdown.append({"factor": f["kind"], "penalty": 0, "detail": "Justificada o corregida: no descuenta."})
            continue
        points = PENALTY.get(f["level"], 3)
        if f["kind"] in recurrent_kinds:
            points += 2  # repeats across periods
        deduct(points, f["kind"], f["message"][:160])

    # Money involved, relative to the period's sales (capped).
    if sales_total > 0:
        involved = sum(Decimal(str(f.get("amount") or 0)) for f in findings if f["level"] != MatchLevel.OK)
        ratio = involved / sales_total
        if ratio > Decimal("0.05"):
            deduct(min(20, int(ratio * 100) // 5 * 2),
                   "AMOUNT_AT_STAKE", f"Los montos observados equivalen al {ratio * 100:.0f}% de las ventas del periodo.")
    if unclassified_credits > 0 and sales_total > 0:
        ratio = unclassified_credits / sales_total
        if ratio > Decimal("0.10"):
            deduct(min(10, int(ratio * 10)), "UNCLASSIFIED_MOVEMENTS",
                   f"S/ {unclassified_credits:,.2f} de abonos aún sin clasificar.")
    return max(0, score), breakdown
