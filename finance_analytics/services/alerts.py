"""Configurable finance alerts, regenerated deterministically.

Each condition produces at most one alert per ``dedup_key``; re-running the
generation updates the explanation but never resets a status a human already
set (en revisión / resuelta / descartada).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from ..models import AlertSeverity, FinanceAlert
from .common import THRESHOLDS
from .consistency import consistency_analysis
from .cpe_summary import load_documents, sales_summary
from .itf_summary import itf_summary
from .parties import customers_analysis, suppliers_analysis

CONSISTENCY_SEVERITY = {
    "high": AlertSeverity.HIGH,
    "medium": AlertSeverity.MEDIUM,
    "low": AlertSeverity.INFO,
}

SOURCE_SALES = "CPE · facturación emitida"
SOURCE_RECEIVED = "CPE · comprobantes recibidos"


def _upsert(account_ruc: str, dedup_key: str, **fields: Any) -> FinanceAlert:
    alert, created = FinanceAlert.objects.get_or_create(
        account_ruc=account_ruc, dedup_key=dedup_key[:160], defaults=fields
    )
    if not created:
        # Refresh the analytical fields; the human-managed status stays.
        for key, value in fields.items():
            setattr(alert, key, value)
        alert.save()
    return alert


def _concentration_alerts(account_ruc: str, customers: dict[str, Any]) -> list[str]:
    keys = []
    summary = customers.get("summary")
    if not summary or not summary.get("top"):
        return keys
    top = summary["top"]
    share = top["share_pct"]
    if share >= THRESHOLDS["concentration_pct"]:
        severity = (
            AlertSeverity.HIGH
            if share >= THRESHOLDS["concentration_critical_pct"]
            else AlertSeverity.MEDIUM
        )
        key = f"concentration:{summary['window']['end']}:{top['ruc']}"
        _upsert(
            account_ruc, key,
            alert_type="concentracion_cliente",
            severity=severity,
            period=summary["window"]["end"],
            title=f"Concentración elevada en {top['name']}",
            explanation=summary["concentration_message"] or "",
            amount=top["net_pen"],
            currency="PEN",
            source=SOURCE_SALES,
            recommendation=(
                "Evaluar diversificación comercial y condiciones con el "
                "cliente principal."
            ),
        )
        keys.append(key)
    return keys


def _sales_drop_alert(account_ruc: str, sales: dict[str, Any]) -> list[str]:
    periods = [p for p in sales["periods"] if p["by_currency"].get("PEN")]
    streak_needed = THRESHOLDS["drop_streak_months"]
    streak = 0
    for row in periods:
        variation = row["variation_pen_pct"]
        if variation is not None and variation < 0:
            streak += 1
        elif variation is not None:
            streak = 0
    if streak < streak_needed or not periods:
        return []
    last = periods[-1]
    key = f"sales_drop:{last['period']}"
    _upsert(
        account_ruc, key,
        alert_type="caida_facturacion",
        severity=AlertSeverity.HIGH,
        period=last["period"],
        title=f"Caída sostenida de facturación ({streak} meses)",
        explanation=(
            f"La facturación neta en soles cae hace {streak} meses "
            f"consecutivos; el último mes varió {last['variation_pen_pct']}%."
        ),
        amount=last["by_currency"]["PEN"]["net"],
        currency="PEN",
        source=SOURCE_SALES,
        recommendation="Revisar pipeline comercial y clientes principales.",
    )
    return [key]


def _supplier_alerts(account_ruc: str, suppliers: dict[str, Any]) -> list[str]:
    keys = []
    summary = suppliers.get("summary") or {}
    window_end = summary.get("window", {}).get("end", "")
    for item in summary.get("unusual_increases", []):
        key = f"supplier_spike:{window_end}:{item['ruc'] or item['name']}"
        _upsert(
            account_ruc, key,
            alert_type="incremento_compras",
            severity=AlertSeverity.MEDIUM,
            period=window_end,
            title=f"Incremento inusual de compras a {item['name']}",
            explanation=(
                f"Las compras del último mes a {item['name']} superan en "
                f"{item['increase_pct']}% su promedio reciente."
            ),
            amount=None,
            currency="PEN",
            source=SOURCE_RECEIVED,
            recommendation="Verificar el sustento de las compras con el área responsable.",
        )
        keys.append(key)
    for row in suppliers.get("parties", []):
        if row["status"] == "nuevo" and (row["net_pen"] or 0) >= THRESHOLDS["new_supplier_amount_pen"]:
            key = f"new_supplier:{window_end}:{row['ruc'] or row['name']}"
            _upsert(
                account_ruc, key,
                alert_type="proveedor_nuevo_gasto_alto",
                severity=AlertSeverity.MEDIUM,
                period=window_end,
                title=f"Proveedor nuevo con gasto elevado: {row['name']}",
                explanation=(
                    f"{row['name']} es un proveedor nuevo con compras por "
                    f"S/ {row['net_pen']:,.0f} en la ventana analizada."
                ),
                amount=row["net_pen"],
                currency="PEN",
                source=SOURCE_RECEIVED,
                recommendation="Confirmar la evaluación del proveedor en Terceros.",
            )
            keys.append(key)
    return keys


def _lost_client_alerts(account_ruc: str, customers: dict[str, Any]) -> list[str]:
    keys = []
    window_end = (customers.get("summary") or {}).get("window", {}).get("end", "")
    for row in customers.get("parties", []):
        if row["status"] == "dejo_de_facturar" and row["share_pct"] >= THRESHOLDS["lost_client_share_pct"]:
            key = f"client_stopped:{window_end}:{row['ruc'] or row['name']}"
            _upsert(
                account_ruc, key,
                alert_type="cliente_dejo_de_facturar",
                severity=AlertSeverity.HIGH,
                period=window_end,
                title=f"{row['name']} dejó de facturar",
                explanation=(
                    f"{row['name']} representaba el {row['share_pct']}% de la "
                    f"facturación de la ventana y no registra comprobantes en "
                    f"los últimos {THRESHOLDS['lost_client_gap_months']} meses."
                ),
                amount=row["net_pen"],
                currency="PEN",
                source=SOURCE_SALES,
                recommendation="Contactar al cliente y revisar la relación comercial.",
            )
            keys.append(key)
    return keys


ITF_VARIATION_FIELD = {
    "inflow": ("variation_inflow_pct", "inflow_base", "entradas"),
    "outflow": ("variation_outflow_pct", "outflow_base", "salidas"),
    "gross": ("variation_gross_pct", "gross_movement", "movimiento bruto"),
}


def _itf_alerts(account_ruc: str, itf: dict[str, Any]) -> list[str]:
    keys = []
    for row in itf["periods"]:
        basis = row.get("atypical_basis")
        if not row["atypical"] or basis not in ITF_VARIATION_FIELD:
            continue
        variation_field, amount_field, short = ITF_VARIATION_FIELD[basis]
        key = f"itf_variation:{row['period']}:{basis}"
        _upsert(
            account_ruc, key,
            alert_type="variacion_itf",
            severity=AlertSeverity.MEDIUM,
            period=row["period"],
            # El título nombra SIEMPRE qué se comparó: entradas, salidas o
            # movimiento bruto. Nunca "el ITF" a secas.
            title=f"Variación de {short} reportadas en {row['label']}",
            explanation=(
                f"Las {row['atypical_basis_label']} variaron "
                f"{row[variation_field]}% frente al mismo concepto del mes "
                f"anterior."
            ),
            amount=row[amount_field],
            currency="PEN",
            source="ITF",
            recommendation=(
                "Contrastar los movimientos del mes con los estados de cuenta; "
                "el reporte ITF no distingue el origen de cada operación."
            ),
        )
        keys.append(key)
    return keys


def _consistency_alerts(account_ruc: str, consistency: dict[str, Any]) -> list[str]:
    keys = []
    for finding in consistency["findings"]:
        key = f"{finding['kind']}:{finding['period']}"
        # Lo que solo está pendiente de clasificar entra como informativo:
        # nunca escala a prioritario ni se presenta como incumplimiento.
        severity = (
            AlertSeverity.INFO
            if finding.get("classification") == "informativo"
            else CONSISTENCY_SEVERITY.get(finding["severity"], AlertSeverity.MEDIUM)
        )
        _upsert(
            account_ruc, key,
            alert_type=finding["kind"],
            severity=severity,
            period=finding["period"],
            # El título es la causa concreta; la explicación, el detalle.
            title=(finding.get("cause") or finding["explanation"])[:200],
            explanation=finding["explanation"],
            amount=finding["amount"],
            currency="PEN",
            source=finding["source"],
            recommendation=finding["recommendation"],
        )
        keys.append(key)
    return keys


def rebuild_alerts(account_ruc: str) -> dict[str, int]:
    ruc = account_ruc
    docs = load_documents(ruc)
    sales = sales_summary(docs)
    customers = customers_analysis(docs)
    suppliers = suppliers_analysis(docs)
    itf = itf_summary(ruc)
    consistency = consistency_analysis(docs, ruc)

    active: list[str] = []
    active += _concentration_alerts(ruc, customers)
    active += _sales_drop_alert(ruc, sales)
    active += _supplier_alerts(ruc, suppliers)
    active += _lost_client_alerts(ruc, customers)
    active += _itf_alerts(ruc, itf)
    active += _consistency_alerts(ruc, consistency)

    return {"active": len(active), "total": FinanceAlert.objects.filter(account_ruc=ruc).count()}
