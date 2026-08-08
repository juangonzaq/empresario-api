"""Customer and supplier analytics over the CPE dataset.

Concentration, recurrence, churn and unusual variations — computed per
currency (PEN drives shares; USD reported apart) with configurable thresholds
from :mod:`.common`. Nothing here asserts deductibility or tax validity of a
purchase; it only measures amounts and behaviour.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

from .common import (
    THRESHOLDS, clean_name, money, pct_change, period_label, period_range_desc,
    window_label,
)
from .cpe_summary import document_amount

# Ventana preferida para concentración y comportamiento: últimos 12 meses.
WINDOW_MONTHS = 12
RECURRENT_MIN_PERIODS = 3
NEW_WINDOW_PERIODS = 2

# La concentración se mide sobre la facturación NETA en soles de la ventana:
# el mismo neto (bruto − Nota crédito + notas de débito) que muestra el
# card de Facturación, para que el % y el monto sean comparables.
SHARE_BASIS = "% sobre la facturación neta en soles de la ventana analizada"


def _party_key(doc: ElectronicInvoice, direction: str) -> tuple[str, str]:
    if direction == Direction.ISSUED:
        return (doc.receiver_ruc or "", clean_name(doc.receiver_name) or "Sin identificar")
    return (doc.issuer_ruc or "", clean_name(doc.issuer_name) or "Sin identificar")


def _signed_amount(doc: ElectronicInvoice) -> Decimal:
    # Mismo importe que usa cpe_summary (con respaldo en el extracto XML), de
    # modo que la suma de las partes reproduzca el neto del periodo.
    amount = document_amount(doc)
    if doc.document_class == DocumentClass.CREDIT_NOTE:
        return -amount
    return amount


def party_analysis(
    docs: list[ElectronicInvoice], direction: str, months: int = WINDOW_MONTHS
) -> dict[str, Any]:
    """Aggregate one direction's counterparties over the last ``months``."""
    relevant = [
        d for d in docs
        if d.direction == direction and not d.is_cancelled and not d.is_rejected
    ]
    if not relevant:
        return {"parties": [], "summary": None, "periods": []}

    latest = max(d.period for d in relevant if d.period)
    window = period_range_desc(latest, months)
    window_set = set(window)

    parties: dict[tuple[str, str], dict[str, Any]] = {}
    for doc in relevant:
        if doc.period not in window_set:
            continue
        key = _party_key(doc, direction)
        p = parties.setdefault(key, {
            "ruc": key[0],
            "name": key[1],
            "by_currency": {},
            "monthly_pen": {},
            "document_count": 0,
            "credit_note_count": 0,
            "credit_notes_pen": Decimal("0"),
            "periods_active": set(),
        })
        currency = doc.currency or "PEN"
        bucket = p["by_currency"].setdefault(
            currency, {"gross": Decimal("0"), "net": Decimal("0")}
        )
        amount = doc.total_amount or Decimal("0")
        signed = _signed_amount(doc)
        bucket["net"] += signed
        p["document_count"] += 1
        if doc.document_class == DocumentClass.INVOICE:
            bucket["gross"] += amount
            p["periods_active"].add(doc.period)
        elif doc.document_class == DocumentClass.CREDIT_NOTE:
            p["credit_note_count"] += 1
            if currency == "PEN":
                p["credit_notes_pen"] += amount
        if currency == "PEN":
            p["monthly_pen"][doc.period] = (
                p["monthly_pen"].get(doc.period, Decimal("0")) + signed
            )

    total_net_pen = sum(
        (p["by_currency"].get("PEN", {}).get("net", Decimal("0")) for p in parties.values()),
        Decimal("0"),
    )

    current, prev = window[-1], window[-2] if len(window) > 1 else None
    recent = set(window[-THRESHOLDS["lost_client_gap_months"]:])
    earlier = window_set - recent

    rows = []
    for p in parties.values():
        active = p.pop("periods_active")
        net_pen = p["by_currency"].get("PEN", {}).get("net", Decimal("0"))
        share = (
            round(float(net_pen / total_net_pen * 100), 1)
            if total_net_pen > 0 and net_pen > 0 else 0.0
        )
        first = min(active) if active else None
        stopped = bool(active) and not (active & recent) and bool(active & earlier)
        if first and first in window[-NEW_WINDOW_PERIODS:]:
            status = "nuevo"
        elif stopped:
            status = "dejo_de_facturar"
        elif len(active) >= RECURRENT_MIN_PERIODS:
            status = "recurrente"
        else:
            status = "ocasional"
        monthly = p.pop("monthly_pen")
        active_months = len([v for v in monthly.values() if v > 0]) or 1
        rows.append({
            **p,
            "by_currency": {
                cur: {k: money(v) for k, v in b.items()}
                for cur, b in p["by_currency"].items()
            },
            "credit_notes_pen": money(p["credit_notes_pen"]),
            "net_pen": money(net_pen),
            "share_pct": share,
            "status": status,
            "first_period": first,
            "monthly": [
                {"period": per, "label": period_label(per), "net_pen": money(monthly.get(per))}
                for per in window if per in monthly
            ],
            "current_pen": money(monthly.get(current)),
            "variation_pct": pct_change(monthly.get(current), monthly.get(prev) if prev else None),
            "avg_monthly_pen": money(net_pen / active_months) if net_pen else 0.0,
        })

    rows.sort(key=lambda r: r["net_pen"] or 0, reverse=True)

    top = rows[0] if rows else None
    threshold = THRESHOLDS["concentration_pct"]
    months_label = window_label(window[0], latest)
    active_periods = sorted({p for r in rows for p in (m["period"] for m in r["monthly"])})

    concentration_msg = None
    if direction == Direction.ISSUED and top and top["share_pct"] >= threshold:
        concentration_msg = (
            f"{top['name']} representa el {top['share_pct']}% de la facturación "
            f"neta de los últimos {months} meses ({months_label}). Existe una "
            f"dependencia comercial elevada (umbral: {threshold:.0f}%)."
        )

    summary = {
        # La ventana se publica siempre, aunque el card no la pida: ningún
        # porcentaje de concentración debe leerse sin saber sobre qué meses se
        # calculó.
        "window": {
            "start": window[0],
            "end": latest,
            "months": months,
            "label": months_label,
            "months_with_activity": len(active_periods),
        },
        "share_basis": SHARE_BASIS,
        "total_net_pen": money(total_net_pen),
        "party_count": len(rows),
        "top": {k: top[k] for k in ("ruc", "name", "net_pen", "share_pct")} if top else None,
        "new_count": sum(1 for r in rows if r["status"] == "nuevo"),
        "recurrent_count": sum(1 for r in rows if r["status"] == "recurrente"),
        "stopped_count": sum(1 for r in rows if r["status"] == "dejo_de_facturar"),
        "concentration_threshold_pct": threshold,
        "concentration_message": concentration_msg,
    }
    return {"parties": rows, "summary": summary, "periods": window}


def customers_analysis(docs: list[ElectronicInvoice], months: int = WINDOW_MONTHS) -> dict[str, Any]:
    return party_analysis(docs, Direction.ISSUED, months)


def suppliers_analysis(docs: list[ElectronicInvoice], months: int = WINDOW_MONTHS) -> dict[str, Any]:
    data = party_analysis(docs, Direction.RECEIVED, months)
    # Unusual increases: current month well above the party's recent average.
    flagged = []
    for row in data["parties"]:
        monthly = row["monthly"]
        if len(monthly) < 2 or not row["current_pen"]:
            continue
        previous_values = [m["net_pen"] for m in monthly[:-1] if m["net_pen"]]
        if not previous_values:
            continue
        avg_prev = sum(previous_values) / len(previous_values)
        variation = pct_change(row["current_pen"], avg_prev)
        if variation is not None and variation >= THRESHOLDS["variation_alert_pct"]:
            row["unusual_increase_pct"] = variation
            flagged.append({"name": row["name"], "ruc": row["ruc"], "increase_pct": variation})
    if data["summary"] is not None:
        data["summary"]["unusual_increases"] = flagged
        data["summary"]["note"] = (
            "Las compras registradas no se califican aquí como deducibles ni "
            "válidas tributariamente; eso requiere revisión contable."
        )
    return data
