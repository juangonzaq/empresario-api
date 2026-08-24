"""SIRE totals vs what the company declared (form 621 figures).

The declared side comes from ``DeclaredSummary`` (SIRE casillas, manual entry
or import). Without a declared row the engine says exactly that — «no hay
declaración registrada para comparar» — instead of inventing a verdict.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from ..models import DeclaredSummary, MatchLevel

DECLARATION_TOLERANCE = Decimal(os.getenv("RECON_DECLARATION_TOLERANCE", "5.00"))
CRITICAL_GAP = Decimal(os.getenv("RECON_DECLARATION_CRITICAL_GAP", "1000"))


def _finding(kind: str, level: str, message: str, amount: Decimal | None = None) -> dict[str, Any]:
    return {"kind": kind, "level": level, "message": message,
            "amount": float(amount) if amount is not None else None}


def _gap(kind: str, label: str, period_label: str, sire: Decimal, declared: Decimal) -> dict[str, Any] | None:
    diff = sire - declared
    if abs(diff) <= DECLARATION_TOLERANCE:
        return None
    level = MatchLevel.CRITICAL if abs(diff) >= CRITICAL_GAP else MatchLevel.REVIEW
    direction = "superan" if diff > 0 else "quedan por debajo de"
    return _finding(
        kind, level,
        f"{label} registradas en SIRE ({sire:,.2f}) {direction} lo declarado ({declared:,.2f}) "
        f"en S/ {abs(diff):,.2f} para el periodo {period_label}. Es una diferencia que requiere revisión, "
        "no una conclusión: puede haber ajustes, ND/NC de periodos anteriores o una rectificatoria en curso.",
        abs(diff),
    )


def compare(account_ruc: str, period: str, sire_sales: dict, sire_purchases: dict) -> dict[str, Any]:
    declared = DeclaredSummary.objects.filter(account_ruc=account_ruc, period=period).first()
    findings: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "declared_available": declared is not None,
        "declared_source": declared.source if declared else None,
        "sales_declared": declared.sales_base if declared else None,
        "purchases_declared": declared.purchases_base if declared else None,
        "igv_declared": declared.igv_payable if declared else None,
    }
    if declared is None:
        findings.append(_finding(
            "DECLARATION_MISSING", MatchLevel.OK,
            f"No hay declaración registrada del periodo {period} para comparar; "
            "regístrala (o espera la sincronización) para cerrar el cruce.",
        ))
        return {"findings": findings, **payload}

    checks = [
        ("SALES_SIRE_VS_DECLARATION", "Las ventas", sire_sales.get("sire_base"), declared.sales_base),
        ("PURCHASES_SIRE_VS_DECLARATION", "Las compras", sire_purchases.get("sire_base"), declared.purchases_base),
        ("IGV_SALES_INCONSISTENT", "El IGV de ventas", sire_sales.get("sire_igv"), declared.sales_igv),
        ("IGV_PURCHASES_INCONSISTENT", "El IGV de compras", sire_purchases.get("sire_igv"), declared.purchases_igv),
    ]
    for kind, label, sire_value, declared_value in checks:
        if sire_value is None or declared_value is None:
            continue
        f = _gap(kind, label, period, Decimal(sire_value), Decimal(declared_value))
        if f:
            findings.append(f)
    if not findings:
        findings.append(_finding("DECLARATION_OK", MatchLevel.OK, f"SIRE y declaración cuadran en el periodo {period}."))
    return {"findings": findings, **payload}
