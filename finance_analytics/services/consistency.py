"""Cross-check between invoicing (CPE) and reported bank movements (ITF).

This is a *detailed cross view*, not a verdict. Every difference found here is
a «diferencia que requiere clasificación o revisión contable» — never an
assertion of evasion, fraud, tax omission, undeclared income or any breach.

Two comparisons are made per month, each against a movement of the matching
direction, never against the gross movement:

* facturación neta emitida  ↔  acreditaciones reportadas (códigos 12 y 13)
* comprobantes recibidos    ↔  débitos reportados (códigos 14 y 15)

Movements whose ``operation_code`` is not in the verified catalog are reported
apart as *pending classification*: they are informational context, they never
count as a finding and never raise the review status.
"""

from __future__ import annotations

from typing import Any

from .common import THRESHOLDS, period_label
from .cpe_summary import purchases_summary, sales_summary
from .itf_summary import itf_summary

REVIEW_NOTE = "Diferencia que requiere clasificación o revisión contable."

NOT_A_BREACH_NOTE = (
    "Una diferencia en este cruce no es un incumplimiento: las fuentes miden "
    "cosas distintas (comprobantes emitidos/recibidos vs. movimientos "
    "bancarios reportados) y su desfase temporal es normal."
)

METHODOLOGY = (
    "Se compara la facturación neta emitida contra las acreditaciones "
    "reportadas (códigos ITF 12 y 13) y los comprobantes recibidos contra los "
    "débitos reportados (códigos ITF 14 y 15). Nunca se compara contra el "
    "movimiento bruto, que suma ambos sentidos. Los movimientos con código sin "
    "catalogar se informan aparte, sin clasificarse como diferencia."
)

# Un lado supera al otro por más de este múltiplo → vale la pena mirarlo.
GAP_HIGH_RATIO = 3.0
GAP_LOW_RATIO = 0.2

# Clasificación de cada hallazgo: qué es y qué no es.
CLASS_REVIEW = "requiere_revision"      # cuenta como hallazgo
CLASS_INFO = "informativo"              # contexto; nunca hallazgo priorizado


def _pen_net(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    return row.get("by_currency", {}).get("PEN", {}).get("net") or 0.0


def _finding(kind: str, period: str, severity: str, explanation: str,
             cause: str, amount: float | None, source: str,
             classification: str = CLASS_REVIEW) -> dict[str, Any]:
    return {
        "kind": kind,
        "period": period,
        "severity": severity,
        "classification": classification,
        "explanation": explanation,
        # Causa concreta en una línea, para el card de Alertas financieras.
        "cause": cause,
        "amount": amount,
        "source": source,
        "recommendation": REVIEW_NOTE,
        "is_breach": False,
    }


def _direction_gap(period: str, label: str, kind: str, cpe_label: str,
                   itf_label: str, cpe_amount: float, itf_amount: float,
                   ) -> dict[str, Any] | None:
    """One side of the cross: facturación↔acreditaciones or compras↔débitos."""
    if not cpe_amount or not itf_amount:
        return None
    if GAP_LOW_RATIO <= itf_amount / cpe_amount <= GAP_HIGH_RATIO:
        return None
    sense = "superan" if itf_amount > cpe_amount else "quedan por debajo de"
    return _finding(
        kind=kind,
        period=period,
        severity="medium",
        explanation=(
            f"En {label} las {itf_label} (S/ {itf_amount:,.0f}) {sense} "
            f"{cpe_label} del mes (S/ {cpe_amount:,.0f}). {NOT_A_BREACH_NOTE}"
        ),
        cause=f"{itf_label.capitalize()} y {cpe_label} descuadran en {label}.",
        amount=itf_amount,
        source="CPE + ITF",
    )


def _period_findings(period: str, itf_row: dict[str, Any], net_sales: float,
                     net_purchases: float) -> list[dict[str, Any]]:
    label = period_label(period)
    inflow = itf_row.get("inflow_base") or 0.0
    outflow = itf_row.get("outflow_base") or 0.0
    unclassified = itf_row.get("unclassified_base") or 0.0
    findings = []

    gap = _direction_gap(period, label, "cpe_itf_gap_entradas",
                         "la facturación neta emitida", "acreditaciones reportadas",
                         net_sales, inflow)
    if gap:
        findings.append(gap)

    gap = _direction_gap(period, label, "cpe_itf_gap_salidas",
                         "los comprobantes recibidos", "débitos reportados",
                         net_purchases, outflow)
    if gap:
        findings.append(gap)

    if inflow and not net_sales:
        findings.append(_finding(
            kind="itf_without_cpe",
            period=period,
            severity="medium",
            explanation=(
                f"En {label} hay acreditaciones reportadas (S/ {inflow:,.0f}) sin "
                f"facturación emitida registrada en CPE ese mes. Puede tratarse "
                f"de cobranzas de meses anteriores, préstamos o transferencias "
                f"propias. {NOT_A_BREACH_NOTE}"
            ),
            cause=f"Acreditaciones sin facturación emitida en {label}.",
            amount=inflow,
            source="ITF",
        ))

    if outflow and not net_purchases:
        findings.append(_finding(
            kind="itf_outflow_without_cpe",
            period=period,
            severity="medium",
            explanation=(
                f"En {label} hay débitos reportados (S/ {outflow:,.0f}) sin "
                f"comprobantes recibidos registrados en CPE ese mes. Puede "
                f"tratarse de pagos de comprobantes anteriores, planilla, "
                f"impuestos o transferencias propias. {NOT_A_BREACH_NOTE}"
            ),
            cause=f"Débitos sin comprobantes recibidos en {label}.",
            amount=outflow,
            source="ITF",
        ))

    if unclassified:
        findings.append(_finding(
            kind="itf_unclassified_movements",
            period=period,
            severity="low",
            explanation=(
                f"En {label} hay S/ {unclassified:,.0f} en movimientos cuyo "
                f"código de operación no está en el catálogo verificado. No se "
                f"les asigna sentido (entrada o salida) ni se cuentan como "
                f"diferencia; quedan pendientes de clasificar."
            ),
            cause=f"Movimientos ITF sin código catalogado en {label}.",
            amount=unclassified,
            source="ITF",
            classification=CLASS_INFO,
        ))

    return findings


def consistency_analysis(docs, months: int = 6) -> dict[str, Any]:
    sales = sales_summary(docs, months=months)
    purchases = purchases_summary(docs, months=months)
    itf = itf_summary(months=months)

    itf_by_period = {p["period"]: p for p in itf["periods"]}
    sales_by_period = {p["period"]: p for p in sales["periods"]}
    purchases_by_period = {p["period"]: p for p in purchases["periods"]}

    overlap = sorted(p for p in sales_by_period if p in itf_by_period)
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for period in overlap:
        itf_row = itf_by_period[period]
        net_sales = _pen_net(sales_by_period.get(period))
        net_purchases = _pen_net(purchases_by_period.get(period))
        inflow = itf_row.get("inflow_base") or 0.0
        outflow = itf_row.get("outflow_base") or 0.0

        rows.append({
            "period": period,
            "label": period_label(period),
            "net_sales_pen": net_sales,
            "received_documents_pen": net_purchases,
            "itf_inflow": inflow,
            "itf_outflow": outflow,
            "itf_unclassified": itf_row.get("unclassified_base") or 0.0,
            "itf_gross_movement": itf_row.get("gross_movement") or 0.0,
            "inflow_vs_sales_ratio": round(inflow / net_sales, 2) if net_sales else None,
            "outflow_vs_received_ratio": (
                round(outflow / net_purchases, 2) if net_purchases else None
            ),
        })
        findings.extend(_period_findings(period, itf_row, net_sales, net_purchases))

    findings.extend(_document_findings(sales))
    findings.extend(_credit_note_findings(sales))

    review = [f for f in findings if f["classification"] == CLASS_REVIEW]
    if not rows:
        status = "sin_datos_cruzados"
    else:
        status = "requiere_revision" if review else "consistente"

    return {
        "status": status,
        "methodology": METHODOLOGY,
        "review_note": REVIEW_NOTE,
        "not_a_breach_note": NOT_A_BREACH_NOTE,
        "rows": rows,
        "findings": findings,
        "review_findings": len(review),
        "informational_findings": len(findings) - len(review),
        "overlap_periods": len(rows),
        "no_overlap_note": (
            "No hay periodos con datos de CPE e ITF a la vez; el cruce se "
            "activará cuando ambas fuentes cubran el mismo mes."
            if not rows else None
        ),
    }


def _document_findings(sales: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for row in sales["periods"]:
        if row["cancelled"]:
            findings.append(_finding(
                kind="cancelled_documents",
                period=row["period"],
                severity="medium",
                explanation=f"{row['cancelled']} comprobante(s) anulado(s) en {row['label']}.",
                cause=f"{row['cancelled']} comprobante(s) anulado(s) en {row['label']}.",
                amount=None,
                source="CPE",
            ))
        if row["rejected"]:
            findings.append(_finding(
                kind="rejected_documents",
                period=row["period"],
                severity="high",
                explanation=f"{row['rejected']} comprobante(s) rechazado(s) en {row['label']}.",
                cause=f"{row['rejected']} comprobante(s) rechazado(s) en {row['label']}.",
                amount=None,
                source="CPE",
            ))
    return findings


def _credit_note_findings(sales: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    threshold = THRESHOLDS["credit_note_ratio_pct"]
    for row in sales["periods"]:
        pen = row["by_currency"].get("PEN")
        if not pen or not pen["gross"]:
            continue
        ratio = pen["credit_notes"] / pen["gross"] * 100
        if ratio >= threshold:
            findings.append(_finding(
                kind="credit_notes_high",
                period=row["period"],
                severity="medium",
                explanation=(
                    f"Las Nota crédito de {row['label']} representan el "
                    f"{ratio:.1f}% de la facturación bruta (umbral: {threshold:.0f}%)."
                ),
                cause=(
                    f"Nota crédito al {ratio:.1f}% de la facturación bruta "
                    f"en {row['label']}."
                ),
                amount=pen["credit_notes"],
                source="CPE",
            ))
    return findings
