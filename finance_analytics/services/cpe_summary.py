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


# Ventana por defecto. Ningún análisis del panel mira más atrás: las series
# usan 13 meses, clientes y proveedores 12, y el cruce de consistencia 6.
# Cargar además los años anteriores solo gastaba memoria.
DEFAULT_WINDOW_MONTHS = 13


def load_documents(
    account_ruc: str, months: int | None = DEFAULT_WINDOW_MONTHS
) -> list[ElectronicInvoice]:
    """Los comprobantes de UNA empresa dentro de la ventana pedida.

    El RUC es obligatorio a propósito: antes caía a ``settings.SUNAT_RUC`` y
    ese respaldo, en un servicio multiempresa, significa servirle a alguien los
    datos de otro. Mejor que reviente el llamador a que devuelva lo ajeno.

    ``months=None`` trae todo el histórico; se usa donde de verdad hace falta
    (el detalle de un mes antiguo). Todo lo demás debe acotarse: con tres años
    de comprobantes, cargar el histórico entero costaba ~68 MB por petición y
    varias peticiones simultáneas de una empresa grande tumbaban el proceso.
    """
    queryset = (
        ElectronicInvoice.objects.for_account(account_ruc)
        .select_related("extract", "override")
        .defer("xml_content", "raw")
    )
    if months:
        floor = _period_floor(months)
        if floor:
            queryset = queryset.filter(period__gte=floor)
    return list(queryset)


def load_manual_entries(
    account_ruc: str, months: int | None = DEFAULT_WINDOW_MONTHS
) -> list["ManualEntry"]:
    """Los registros manuales de la empresa, con la misma ventana que los
    comprobantes: los totales mensuales suman lo manual y lo automático, así
    que ambos deben cubrir los mismos meses."""
    from finance_analytics.models import ManualEntry

    queryset = ManualEntry.objects.filter(account_ruc=account_ruc)
    if months:
        floor = _period_floor(months)
        if floor:
            queryset = queryset.filter(period__gte=floor)
    return list(queryset)


def _period_floor(months: int) -> str:
    """El periodo más antiguo que hay que traer, a partir del más reciente.

    Se ancla al último periodo con datos, no a la fecha de hoy: una empresa que
    dejó de facturar hace medio año debe seguir viendo su último año con
    movimiento, no una pantalla vacía.
    """
    import datetime

    from django.utils import timezone

    hoy = timezone.localdate()
    year, month = hoy.year, hoy.month - (months - 1)
    while month <= 0:
        year -= 1
        month += 12
    return f"{year}{month:02d}"


def document_amount(doc: ElectronicInvoice) -> Decimal:
    """Importe del comprobante: la corrección manual manda; si no hay, el
    total declarado o, si falta, el del extracto XML. Compartido por todos
    los agregados para que los totales cuadren."""
    override = getattr(doc, "override", None)
    if override and override.total_amount is not None:
        return override.total_amount
    if doc.total_amount is not None:
        return doc.total_amount
    extract = getattr(doc, "extract", None)
    if extract and extract.total_amount is not None:
        return extract.total_amount
    return Decimal("0")


def document_is_edited(doc: ElectronicInvoice) -> bool:
    """True cuando el comprobante tiene una corrección manual aplicada."""
    override = getattr(doc, "override", None)
    return bool(
        override
        and (override.total_amount is not None or override.counterparty or override.note)
    )


def document_counterparty(doc: ElectronicInvoice, direction: str) -> str:
    """La contraparte mostrada: la corregida a mano si existe."""
    override = getattr(doc, "override", None)
    if override and override.counterparty:
        return override.counterparty
    return (
        doc.receiver_name if direction == Direction.ISSUED else doc.issuer_name
    )


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
        # Registros manuales del mes: entran al neto junto con lo automático.
        "manual": Decimal("0"),
        "manual_count": 0,
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


def summarize_documents(
    docs: Iterable[ElectronicInvoice], manual: Iterable[Any] = (),
) -> dict[str, Any]:
    """Aggregate one period's documents of a single direction, per currency.

    ``manual`` son los :class:`~finance_analytics.models.ManualEntry` del
    mismo mes y dirección: entran a su moneda y el neto los incluye, porque
    el total mensual se calcula sobre lo manual y lo automático juntos.
    """
    by_currency: dict[str, dict[str, Any]] = {}
    cancelled = rejected = 0
    for doc in docs:
        if doc.is_cancelled:
            cancelled += 1
        elif doc.is_rejected:
            rejected += 1
        else:
            _accumulate(by_currency.setdefault(_currency(doc), _blank_bucket()), doc)

    for entry in manual:
        bucket = by_currency.setdefault(entry.currency or "PEN", _blank_bucket())
        bucket["manual"] += entry.amount
        bucket["manual_count"] += 1

    for bucket in by_currency.values():
        bucket["net"] = (
            bucket["gross"] - bucket["credit_notes"] + bucket["debit_notes"]
            + bucket["manual"]
        )

    return {
        "by_currency": {
            cur: {key: (money(v) if isinstance(v, Decimal) else v) for key, v in b.items()}
            for cur, b in by_currency.items()
        },
        "cancelled": cancelled,
        "rejected": rejected,
    }


def direction_series(
    docs: list[ElectronicInvoice], direction: str, months: int = DEFAULT_MONTHS,
    manual: list[Any] | None = None,
) -> dict[str, Any]:
    """Per-period series for one direction, ending at the latest period seen.

    ``manual`` (los ``ManualEntry`` de la empresa) se filtra por dirección y
    entra a cada mes junto con los comprobantes: un mes con solo registros
    manuales también aparece en la serie.
    """
    relevant = [d for d in docs if d.direction == direction]
    manual_relevant = [e for e in (manual or []) if e.direction == direction]
    if not relevant and not manual_relevant:
        # Mismas claves que el caso con datos: una empresa recién creada no
        # tiene comprobantes, y el contrato de salida no puede cambiar de
        # forma según haya datos o no.
        return {
            "periods": [], "current": None, "previous": None,
            "latest_period": None,
        }

    latest = max(
        [d.period for d in relevant if d.period]
        + [e.period for e in manual_relevant if e.period]
    )
    window = period_range_desc(latest, months)
    by_period: dict[str, list[ElectronicInvoice]] = {p: [] for p in window}
    for doc in relevant:
        if doc.period in by_period:
            by_period[doc.period].append(doc)
    manual_by_period: dict[str, list[Any]] = {p: [] for p in window}
    for entry in manual_relevant:
        if entry.period in manual_by_period:
            manual_by_period[entry.period].append(entry)

    periods = []
    # La variación mensual compara SIEMPRE neto PEN contra neto PEN del mes
    # inmediatamente anterior de la ventana. Un mes sin comprobantes vale 0 y
    # no se "salta": arrastrar el neto de un mes lejano rompería la etiqueta
    # «vs mes anterior».
    previous: tuple[str, float] | None = None
    for period in window:
        summary = summarize_documents(by_period[period], manual_by_period[period])
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


def sales_summary(
    docs: list[ElectronicInvoice], months: int = DEFAULT_MONTHS,
    manual: list[Any] | None = None,
) -> dict[str, Any]:
    data = direction_series(docs, Direction.ISSUED, months, manual)
    data["meaning"] = (
        "Facturación emitida según CPE más ingresos registrados a mano. "
        "Representa ventas facturadas, no cobranza ni caja."
    )
    return data


def purchases_summary(
    docs: list[ElectronicInvoice], months: int = DEFAULT_MONTHS,
    manual: list[Any] | None = None,
) -> dict[str, Any]:
    data = direction_series(docs, Direction.RECEIVED, months, manual)
    data["meaning"] = (
        "Comprobantes recibidos de proveedores más gastos registrados a "
        "mano. Son compras registradas, no ingresos ni egresos de caja."
    )
    return data


def period_documents_for(
    account_ruc: str, period: str, direction: str, currency: str | None = None
) -> dict[str, Any]:
    """Igual que :func:`period_documents`, pero pidiendo a la base solo ese mes.

    Antes se cargaba el histórico completo para quedarse con un mes. Ahora la
    consulta ya viene acotada, que es lo que permite abrir el detalle de un mes
    antiguo sin traerse tres años.
    """
    docs = list(
        ElectronicInvoice.objects.for_account(account_ruc)
        .filter(period=period, direction=direction)
        .select_related("extract", "override")
        .defer("xml_content", "raw")
    )
    from finance_analytics.models import ManualEntry

    manual = list(
        ManualEntry.objects.filter(
            account_ruc=account_ruc, period=period, direction=direction
        )
    )
    return period_documents(docs, period, direction, currency, manual)


def manual_entry_payload(entry: Any) -> dict[str, Any]:
    """Un registro manual tal como lo consume la UI, en todas las vistas."""
    return {
        "id": str(entry.id),
        "direction": entry.direction,
        "kind": "ingreso" if entry.direction == Direction.ISSUED else "gasto",
        "entry_date": entry.entry_date,
        "period": entry.period,
        "description": entry.description,
        "counterparty": entry.counterparty,
        "currency": entry.currency or "PEN",
        "amount": money(entry.amount),
        "note": entry.note,
        "category_code": getattr(entry, "category_code", ""),
        "origin": "manual",
    }


def period_documents(
    docs: list[ElectronicInvoice], period: str, direction: str,
    currency: str | None = None, manual: list[Any] | None = None,
) -> dict[str, Any]:
    """Los comprobantes de un mes y una dirección, con el mismo total que la
    fila de la tabla: los importes salen de ``summarize_documents``, no de una
    suma aparte, así el detalle nunca discrepa del resumen que lo abrió.
    Los registros manuales del mes van en ``manual_entries`` y ya están
    dentro de los totales."""
    month = [d for d in docs if d.period == period and d.direction == direction]
    month_manual = [
        e for e in (manual or [])
        if e.period == period and e.direction == direction
    ]
    if currency:
        month = [d for d in month if _currency(d) == currency]
        month_manual = [e for e in month_manual if (e.currency or "PEN") == currency]

    summary = summarize_documents(month, month_manual)
    rows = [
        {
            "id": str(doc.id),
            "document_class": doc.document_class,
            "full_number": doc.full_number or f"{doc.series}-{doc.number}",
            "issue_date": doc.issue_date,
            "counterparty": clean_name(document_counterparty(doc, direction))
            or "Sin identificar",
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
            "origin": "sunat",
            # true cuando hay corrección manual: la UI lo señala y ofrece
            # restaurar los valores de SUNAT.
            "edited": document_is_edited(doc),
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
        "manual_entries": [
            manual_entry_payload(e)
            for e in sorted(
                month_manual,
                key=lambda e: (e.entry_date, e.created_at),
                reverse=True,
            )
        ],
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
