"""Orchestrator: one call reconciles a company+period end to end.

Order per spec: deterministic first (documents, declarations, banking, ITF),
alerts and score persisted, AI never involved here. The run works with the
sources that exist — a missing side is reported as unavailable, not as a
finding against the company.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.utils import timezone

from finance_analytics.models import FinanceAlert

from ..models import (
    ConsistencyScore, DocMatchStatus, DocumentReconciliation, MatchLevel,
    ReconciliationRun, RunStatus,
)
from . import alerts as alert_engine
from . import banking, cpe_sire, declarations, itf_analysis, matching, score as score_engine


def _previous_period(period: str) -> str:
    year, month = int(period[:4]), int(period[4:])
    return f"{year - 1}12" if month == 1 else f"{year}{month - 1:02d}"


def _document_findings(period: str, rows: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
    """Aggregate per-document rows into period-level findings for alerts/score."""
    label = "emitidos" if direction == "sales" else "recibidos"
    findings = []
    missing = [r for r in rows if r["status"] == DocMatchStatus.CPE_ONLY and r["level"] != MatchLevel.OK]
    if missing:
        amount = float(sum(Decimal(str(r.get("cpe_total") or 0)) for r in missing))
        worst = max(r["level"] for r in missing)
        findings.append({
            "kind": "CPE_NOT_IN_SIRE", "level": worst, "amount": amount,
            "message": (f"{len(missing)} comprobante(s) {label} del periodo {period} no aparecen en el registro "
                        f"SIRE (S/ {amount:,.2f}). Puede ser desfase de generación del registro: requiere revisión."),
            "direction": direction,
        })
    orphan = [r for r in rows if r["status"] == DocMatchStatus.SIRE_ONLY and r["level"] != MatchLevel.OK]
    if orphan:
        amount = float(sum(Decimal(str(r.get("sire_total") or 0)) for r in orphan))
        findings.append({
            "kind": "SIRE_NOT_IN_CPE", "level": max(r["level"] for r in orphan), "amount": amount,
            "message": (f"{len(orphan)} registro(s) SIRE de {label} sin comprobante electrónico asociado "
                        f"(S/ {amount:,.2f}) en {period}. Revisa comprobantes físicos, importaciones o recibos."),
            "direction": direction,
        })
    diffs = [r for r in rows if r["status"] in (DocMatchStatus.AMOUNT_MISMATCH, DocMatchStatus.IGV_MISMATCH)]
    if diffs:
        amount = float(sum(abs(Decimal(str(r["differences"].get("amount_diff") or r["differences"].get("igv_diff") or 0))) for r in diffs))
        findings.append({
            "kind": "CPE_SIRE_AMOUNT", "level": max(r["level"] for r in diffs), "amount": amount,
            "message": f"{len(diffs)} comprobante(s) {label} difieren de monto o IGV entre CPE y SIRE en {period}.",
            "direction": direction,
        })
    return findings


def run_reconciliation(account_ruc: str, period: str) -> ReconciliationRun:
    run = ReconciliationRun.objects.create(account_ruc=account_ruc, period=period)
    try:
        # 1 · documents (CPE ↔ SIRE), both directions, persisted row by row
        sales = cpe_sire.reconcile_direction(account_ruc, period, "sales")
        purchases = cpe_sire.reconcile_direction(account_ruc, period, "purchases")
        DocumentReconciliation.objects.filter(account_ruc=account_ruc, period=period).delete()
        for direction, result in (("sales", sales), ("purchases", purchases)):
            DocumentReconciliation.objects.bulk_create([
                DocumentReconciliation(
                    run=run, account_ruc=account_ruc, period=period, direction=direction,
                    doc_key=r["doc_key"], counterparty_ruc=r["counterparty_ruc"][:15],
                    counterparty_name=r["counterparty_name"][:200],
                    cpe_id=r["cpe_id"], sire_id=r["sire_id"] if isinstance(r["sire_id"], int) else None,
                    status=r["status"], level=r["level"],
                    cpe_total=r["cpe_total"], sire_total=r["sire_total"],
                    cpe_igv=r["cpe_igv"], sire_igv=r["sire_igv"], differences=r["differences"],
                ) for r in result["rows"]
            ])

        findings: list[dict[str, Any]] = []
        if sales["totals"]["sire_available"]:
            findings += _document_findings(period, sales["rows"], "sales")
        if purchases["totals"]["sire_available"]:
            findings += _document_findings(period, purchases["rows"], "purchases")

        # 2 · SIRE vs declaration
        decl = declarations.compare(account_ruc, period, sales["totals"], purchases["totals"])
        findings += [f for f in decl["findings"]]

        # 3 · banking: settle invoices, classify the rest, measure the pending
        matching.rebuild_settlements(account_ruc)
        banking.classify_movements(account_ruc)
        pending = banking.pending_amount(account_ruc, period)
        if pending > 0:
            findings.append({
                "kind": "BANK_CREDITS_UNCLASSIFIED", "level": MatchLevel.WARNING, "amount": float(pending),
                "message": (f"S/ {pending:,.2f} en abonos del periodo {period} siguen pendientes de clasificar. "
                            "No se asume que sean ventas: clasifícalos para cerrar el cruce."),
            })

        # 4 · ITF contrast
        sales_total = sales["totals"]["cpe_total"]
        itf = itf_analysis.analyze(account_ruc, period, _previous_period(period), sales_total)
        findings += itf["findings"]

        # 5 · alerts (dedup + human status preserved)
        alert_engine.sync_findings(account_ruc, period, findings)
        alerts_by_kind = {
            a.dedup_key.split(":")[1]: a
            for a in FinanceAlert.objects.filter(account_ruc=account_ruc, dedup_key__startswith="recon:", period=period)
        }

        # 6 · score
        recurrent = {
            k.split(":")[1] for k in FinanceAlert.objects.filter(
                account_ruc=account_ruc, dedup_key__startswith="recon:",
            ).exclude(period=period).values_list("dedup_key", flat=True)
        }
        value, breakdown = score_engine.compute(
            findings, alerts_by_kind, sales_total, pending, recurrent,
        )
        ConsistencyScore.objects.update_or_create(
            account_ruc=account_ruc, period=period,
            defaults={"run": run, "score": value, "breakdown": breakdown},
        )

        counters: dict[str, int] = {}
        for f in findings:
            if f["level"] != MatchLevel.OK:
                counters[f["level"]] = counters.get(f["level"], 0) + 1
        run.totals = {
            "sales_cpe": float(sales["totals"]["cpe_total"]),
            "sales_sire": float(sales["totals"]["sire_total"]) if sales["totals"]["sire_available"] else None,
            "sales_declared": float(decl["sales_declared"]) if decl.get("sales_declared") is not None else None,
            "purchases_cpe": float(purchases["totals"]["cpe_total"]),
            "purchases_sire": float(purchases["totals"]["sire_total"]) if purchases["totals"]["sire_available"] else None,
            "purchases_declared": float(decl["purchases_declared"]) if decl.get("purchases_declared") is not None else None,
            "igv_declared": float(decl["igv_declared"]) if decl.get("igv_declared") is not None else None,
            "itf_credits": float(itf["credits"]), "itf_debits": float(itf["debits"]),
            "bank_pending": float(pending),
            "declared_available": decl["declared_available"],
            "sire_available": sales["totals"]["sire_available"] or purchases["totals"]["sire_available"],
            "score": value,
        }
        run.findings_count = counters
        run.status = RunStatus.DONE
    except Exception as exc:  # noqa: BLE001 — the run must record its own failure
        run.status = RunStatus.FAILED
        run.error = str(exc)[:2000]
        raise
    finally:
        run.finished_at = timezone.now()
        run.save()
    return run
