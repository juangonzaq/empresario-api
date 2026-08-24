"""Normalization layer: every source becomes the same small record.

The engine never touches raw model quirks; it reconciles ``NormalizedDoc``
records, no matter whether they came from a CPE or from a SIRE registry row.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

# SUNAT catalog 01 document types worth reconciling row by row.
INVOICE = "01"
BOLETA = "03"
CREDIT_NOTE = "07"
DEBIT_NOTE = "08"


def normalize_series(series: str | None) -> str:
    return (series or "").strip().upper()


def normalize_number(number: str | None) -> str:
    n = (number or "").strip().lstrip("0")
    return n or "0"


def normalize_doc_type(doc_type: str | None) -> str:
    d = (doc_type or "").strip()
    return d.zfill(2) if d.isdigit() else d.upper()


def doc_key(doc_type: str | None, series: str | None, number: str | None) -> str:
    return f"{normalize_doc_type(doc_type)}-{normalize_series(series)}-{normalize_number(number)}"


@dataclass
class NormalizedDoc:
    source: str                       # "cpe" | "sire"
    source_id: str | int
    direction: str                    # DocDirection value
    doc_type: str
    series: str
    number: str
    issue_date: datetime.date | None
    counterparty_ruc: str
    counterparty_name: str
    base_amount: Decimal | None
    igv: Decimal | None
    total: Decimal | None
    currency: str = "PEN"
    cancelled: bool = False
    is_credit_note: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return doc_key(self.doc_type, self.series, self.number)


def _cpe_igv(doc: ElectronicInvoice) -> Decimal | None:
    extract = getattr(doc, "extract", None)
    return extract.igv_amount if extract and extract.igv_amount is not None else None


def _cpe_total(doc: ElectronicInvoice) -> Decimal | None:
    from finance_analytics.services.cpe_summary import document_amount

    total = document_amount(doc)
    return total if total else (doc.total_amount or None)


def from_cpe(doc: ElectronicInvoice) -> NormalizedDoc:
    total = _cpe_total(doc)
    igv = _cpe_igv(doc)
    base = (total - igv) if (total is not None and igv is not None) else None
    if doc.direction == Direction.ISSUED:
        ruc, name = doc.receiver_ruc, doc.receiver_name
    else:
        ruc, name = doc.issuer_ruc, doc.issuer_name
    return NormalizedDoc(
        source="cpe", source_id=str(doc.pk), direction=("sales" if doc.direction == Direction.ISSUED else "purchases"),
        doc_type=doc.document_type or ("07" if doc.document_class == DocumentClass.CREDIT_NOTE else ""),
        series=doc.series, number=doc.number, issue_date=doc.issue_date if hasattr(doc, "issue_date") else None,
        counterparty_ruc=(ruc or "").strip(), counterparty_name=(name or "").strip(),
        base_amount=base, igv=igv, total=total,
        currency=(doc.currency or "PEN") or "PEN",
        cancelled=bool(doc.is_cancelled or doc.is_rejected),
        is_credit_note=doc.document_class == DocumentClass.CREDIT_NOTE,
    )


def from_sire(row, direction: str) -> NormalizedDoc:
    """``row`` is a ``sensor_sunat.SalesDoc`` or ``PurchaseDoc``."""
    if direction == "sales":
        ruc, name = row.customer_ruc, row.customer_name
    else:
        ruc, name = row.supplier_ruc, row.supplier_name
    return NormalizedDoc(
        source="sire", source_id=row.pk, direction=direction,
        doc_type=row.doc_type, series=row.series, number=row.number,
        issue_date=row.issue_date,
        counterparty_ruc=(ruc or "").strip(), counterparty_name=(name or "").strip(),
        base_amount=row.base_amount, igv=row.igv, total=row.total,
        is_credit_note=normalize_doc_type(row.doc_type) == CREDIT_NOTE,
        extra={"car_sunat": row.car_sunat},
    )


def load_cpe(account_ruc: str, period: str, direction: str) -> list[NormalizedDoc]:
    qs = (
        ElectronicInvoice.objects.for_account(account_ruc)
        .filter(period=period, direction=Direction.ISSUED if direction == "sales" else Direction.RECEIVED)
        .select_related("extract", "override").defer("xml_content", "raw")
    )
    return [from_cpe(d) for d in qs]


def load_sire(account_ruc: str, period: str, direction: str) -> list[NormalizedDoc]:
    """SIRE rows for the period.

    ``sensor_sunat`` predates multi-company support and stores rows without a
    RUC column: they belong to ``settings.SUNAT["RUC"]``. Until that app is
    made tenant-aware, other companies simply get an empty SIRE side (the
    engine reports it as «SIRE sin sincronizar», never as a mismatch).
    """
    from django.conf import settings

    from sensor_sunat.models import PurchaseDoc, SalesDoc

    if account_ruc != settings.SUNAT.get("RUC"):
        return []
    model = SalesDoc if direction == "sales" else PurchaseDoc
    return [from_sire(r, direction) for r in model.objects.filter(tax_period=period)]
