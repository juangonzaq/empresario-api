"""Turns engine findings into ``FinanceAlert`` rows.

Reuses the existing alert infrastructure (dedup by key, human-managed status
that survives recalculation, the alerts screen the product already has). A
finding whose alert was marked justified/corrected/dismissed keeps that state
on re-run — and the score engine skips it.
"""

from __future__ import annotations

from typing import Any

from finance_analytics.models import AlertSeverity, FinanceAlert

from ..models import MatchLevel

LEVEL_TO_SEVERITY = {
    MatchLevel.OK: AlertSeverity.INFO,
    MatchLevel.WARNING: AlertSeverity.MEDIUM,
    MatchLevel.REVIEW: AlertSeverity.HIGH,
    MatchLevel.CRITICAL: AlertSeverity.CRITICAL,
}

TITLES = {
    "CPE_NOT_IN_SIRE": "CPE sin registro en SIRE",
    "SIRE_NOT_IN_CPE": "Registro SIRE sin CPE",
    "CPE_SIRE_AMOUNT": "Diferencias de monto CPE vs SIRE",
    "SALES_SIRE_VS_DECLARATION": "Ventas SIRE vs declaración",
    "PURCHASES_SIRE_VS_DECLARATION": "Compras SIRE vs declaración",
    "IGV_SALES_INCONSISTENT": "IGV de ventas inconsistente",
    "IGV_PURCHASES_INCONSISTENT": "IGV de compras inconsistente",
    "ITF_OUT_OF_PROPORTION": "Movimiento bancario desproporcionado frente a ventas",
    "ITF_ABOVE_SALES": "Abonos bancarios por encima de las ventas",
    "BANK_CREDITS_UNCLASSIFIED": "Movimientos bancarios sin clasificar",
    "INVOICES_UNPAID": "Facturas sin cobro identificado",
}


def upsert(account_ruc: str, period: str, kind: str, level: str, message: str,
           amount: float | None = None, extra_key: str = "") -> FinanceAlert:
    dedup = f"recon:{kind}:{period}" + (f":{extra_key}" if extra_key else "")
    alert, created = FinanceAlert.objects.get_or_create(
        account_ruc=account_ruc, dedup_key=dedup[:160],
        defaults={
            "alert_type": f"recon_{kind.lower()}"[:50],
            "severity": LEVEL_TO_SEVERITY.get(level, AlertSeverity.MEDIUM),
            "period": period,
            "title": TITLES.get(kind, kind.replace("_", " ").capitalize()),
            "explanation": message,
            "amount": amount,
        },
    )
    if not created:
        # Refresh facts; never touch the human-managed status.
        alert.severity = LEVEL_TO_SEVERITY.get(level, alert.severity)
        alert.explanation = message
        alert.amount = amount
        alert.save(update_fields=["severity", "explanation", "amount", "updated_at"])
    return alert


def sync_findings(account_ruc: str, period: str, findings: list[dict[str, Any]]) -> list[FinanceAlert]:
    out = []
    for f in findings:
        if f["level"] == MatchLevel.OK:
            continue
        out.append(upsert(account_ruc, period, f["kind"], f["level"], f["message"], f.get("amount")))
    return out
