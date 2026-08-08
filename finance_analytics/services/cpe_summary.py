"""Executive CPE aggregates: issued sales and received purchases per period.

Rules encoded here, not left to the reader:
* Issued invoices are FACTURACIÓN, never collections; received ones are never
  income — the two directions never mix.
* Credit notes reduce the related side's total; debit notes add to it.
* Cancelled and rejected documents are counted but excluded from amounts.
* Currencies are never added together: every total is ``{currency: amount}``.
* Aggregation runs over the full queryset (all pages), deduplicated by the
  model's unique constraint upstream.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from django.conf import settings

from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

from .common import clean_name, money, pct_change, period_label, period_range_desc

DEFAULT_MONTHS = 13

# Qué compara `variation_pen_pct`: neto contra neto, nunca bruto contra neto.
VARIATION_BASIS = "neto en soles vs neto en soles del mes anterior"


def load_documents(account_ruc: str | None = None) -> list[ElectronicInvoice]:
    """The whole CPE dataset in one query; 600–10k rows is trivial in memory
    and lets every analysis share a single load."""
    ruc = account_ruc or settings.SUNAT_RUC
    return list(
        ElectronicInvoice.objects.for_account(ruc)
        .select_related("extract")
        .defer("xml_content", "raw")
    )


def document_amount(doc: ElectronicInvoice) -> Decimal:
    """Importe del comprobante: total declarado o, si falta, el del extracto
    XML. Compartido por todos los agregados para que los totales cuadren."""
    if doc.total_amount is not None:
        return doc.total_amount
    extract = getattr(doc, "extract", None)
    if extract and extract.total_amount is not None:
        return extract.total_amount
    return Decimal("0")


def _igv(doc: ElectronicInvoice) -> Decimal | None:
    extract = getattr(doc, "extract", None)
    return extract.igv_amount if extract else None


def _currency(doc: ElectronicInvoice) -> str:
    return doc.currency or "PEN"


def _blank_bucket() -> dict[str, Any]:
    return {
        "gross": Decimal("0"),
        "credit_notes": Decimal("0"),
        "debit_notes": Decimal("0"),
        "net": Decimal("0"),
        "igv": Decimal("0"),
        "igv_available": 0,
        "invoice_count": 0,
        "credit_note_count": 0,
    }


def _accumulate(bucket: dict[str, Any], doc: ElectronicInvoice) -> None:
    amount = document_amount(doc)
    igv = _igv(doc)
    if doc.document_class == DocumentClass.INVOICE:
        bucket["gross"] += amount
        bucket["invoice_count"] += 1
        if igv is not None:
            bucket["igv"] += igv
            bucket["igv_available"] += 1
    elif doc.document_class == DocumentClass.CREDIT_NOTE:
        bucket["credit_notes"] += amount
        bucket["credit_note_count"] += 1
        if igv is not None:
            bucket["igv"] -= igv
    elif doc.document_class == DocumentClass.DEBIT_NOTE:
        bucket["debit_notes"] += amount


def summarize_documents(docs: Iterable[ElectronicInvoice]) -> dict[str, Any]:
    """Aggregate one period's documents of a single direction, per currency."""
    by_currency: dict[str, dict[str, Any]] = {}
    cancelled = rejected = 0
    for doc in docs:
        if doc.is_cancelled:
            cancelled += 1
        elif doc.is_rejected:
            rejected += 1
        else:
            _accumulate(by_currency.setdefault(_currency(doc), _blank_bucket()), doc)

    for bucket in by_currency.values():
        bucket["net"] = bucket["gross"] - bucket["credit_notes"] + bucket["debit_notes"]

    return {
        "by_currency": {
            cur: {key: (money(v) if isinstance(v, Decimal) else v) for key, v in b.items()}
            for cur, b in by_currency.items()
        },
        "cancelled": cancelled,
        "rejected": rejected,
    }


def direction_series(
    docs: list[ElectronicInvoice], direction: str, months: int = DEFAULT_MONTHS
) -> dict[str, Any]:
    """Per-period series for one direction, ending at the latest period seen."""
    relevant = [d for d in docs if d.direction == direction]
    if not relevant:
        return {"periods": [], "current": None, "latest_period": None}

    latest = max(d.period for d in relevant if d.period)
    window = period_range_desc(latest, months)
    by_period: dict[str, list[ElectronicInvoice]] = {p: [] for p in window}
    for doc in relevant:
        if doc.period in by_period:
            by_period[doc.period].append(doc)

    periods = []
    # La variación mensual compara SIEMPRE neto PEN contra neto PEN del mes
    # inmediatamente anterior de la ventana. Un mes sin comprobantes vale 0 y
    # no se "salta": arrastrar el neto de un mes lejano rompería la etiqueta
    # «vs mes anterior».
    previous: tuple[str, float] | None = None
    for period in window:
        summary = summarize_documents(by_period[period])
        net_pen = summary["by_currency"].get("PEN", {}).get("net")
        row = {
            "period": period,
            "label": period_label(period),
            **summary,
            "gross_pen": summary["by_currency"].get("PEN", {}).get("gross"),
            "credit_notes_pen": summary["by_currency"].get("PEN", {}).get("credit_notes"),
            "net_pen": net_pen,
            "previous_period": previous[0] if previous else None,
            "previous_net_pen": previous[1] if previous else None,
            "variation_pen_pct": pct_change(net_pen, previous[1] if previous else None),
            "variation_basis": VARIATION_BASIS,
        }
        periods.append(row)
        previous = (period, net_pen if net_pen is not None else 0.0)

    return {
        "periods": periods,
        "current": periods[-1] if periods else None,
        "previous": periods[-2] if len(periods) > 1 else None,
        "latest_period": latest,
    }


def sales_summary(docs: list[ElectronicInvoice], months: int = DEFAULT_MONTHS) -> dict[str, Any]:
    data = direction_series(docs, Direction.ISSUED, months)
    data["meaning"] = (
        "Facturación emitida según CPE. Representa ventas facturadas, no "
        "cobranza ni caja."
    )
    return data


def purchases_summary(docs: list[ElectronicInvoice], months: int = DEFAULT_MONTHS) -> dict[str, Any]:
    data = direction_series(docs, Direction.RECEIVED, months)
    data["meaning"] = (
        "Comprobantes recibidos de proveedores. Son compras registradas, no "
        "ingresos ni egresos de caja."
    )
    return data


def period_documents(
    docs: list[ElectronicInvoice], period: str, direction: str,
    currency: str | None = None,
) -> dict[str, Any]:
    """Los comprobantes de un mes y una dirección, con el mismo total que la
    fila de la tabla: los importes salen de ``summarize_documents``, no de una
    suma aparte, así el detalle nunca discrepa del resumen que lo abrió."""
    month = [d for d in docs if d.period == period and d.direction == direction]
    if currency:
        month = [d for d in month if _currency(d) == currency]

    summary = summarize_documents(month)
    rows = [
        {
            "id": str(doc.id),
            "document_class": doc.document_class,
            "full_number": doc.full_number or f"{doc.series}-{doc.number}",
            "issue_date": doc.issue_date,
            "counterparty": clean_name(
                doc.receiver_name if direction == Direction.ISSUED else doc.issuer_name
            ) or "Sin identificar",
            "counterparty_ruc": (
                doc.receiver_ruc if direction == Direction.ISSUED else doc.issuer_ruc
            ),
            "currency": _currency(doc),
            "amount": money(document_amount(doc)),
            # Las notas de crédito restan del neto del mes; el signo se muestra.
            "subtracts": doc.document_class == DocumentClass.CREDIT_NOTE,
            "is_cancelled": doc.is_cancelled,
            "is_rejected": doc.is_rejected,
            "status": doc.status,
            "references_document": doc.references_document,
        }
        for doc in sorted(
            month,
            key=lambda d: (d.issue_date or d.created_at.date(), d.full_number or ""),
            reverse=True,
        )
    ]

    return {
        "period": period,
        "label": period_label(period),
        "direction": direction,
        "currency": currency,
        "totals": summary["by_currency"].get(currency) if currency else None,
        "by_currency": summary["by_currency"],
        "cancelled": summary["cancelled"],
        "rejected": summary["rejected"],
        "documents": rows,
    }


def credit_notes_detail(docs: list[ElectronicInvoice], months: int = 12) -> dict[str, Any]:
    """Every credit note with its reason and the document it reduces."""
    notes = [d for d in docs if d.document_class == DocumentClass.CREDIT_NOTE]
    if not notes:
        return {"notes": [], "by_period": []}
    latest = max((d.period for d in notes if d.period), default=None)
    window = set(period_range_desc(latest, months)) if latest else set()

    rows = []
    for doc in sorted(notes, key=lambda d: (d.period, d.issue_date or d.created_at.date()), reverse=True):
        if window and doc.period not in window:
            continue
        extract = getattr(doc, "extract", None)
        rows.append({
            "id": str(doc.id),
            "direction": doc.direction,
            "full_number": doc.full_number or f"{doc.series}-{doc.number}",
            "issue_date": doc.issue_date,
            "period": doc.period,
            "currency": _currency(doc),
            "amount": money(document_amount(doc)),
            "counterparty": clean_name(
                doc.receiver_name if doc.direction == Direction.ISSUED else doc.issuer_name
            ),
            "references_document": doc.references_document
            or (extract.reference_id if extract else ""),
            "reason": extract.reference_reason if extract else "",
        })
    return {"notes": rows}
