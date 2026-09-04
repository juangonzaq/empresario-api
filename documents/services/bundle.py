"""Qué entra en una descarga masiva y cómo se arma el .zip.

El archivo sigue el orden de la carpeta de comprobantes en disco —año, mes,
tipo— con el XML firmado y el PDF (representación impresa) de cada factura o
nota, o el PDF de cada recibo por honorarios, más un ``indice.csv`` que el
contador abre en Excel. Todo se genera en la misma petición: a unos 4 ms por
PDF, el tope de documentos por descarga cabe de sobra en el tiempo de
respuesta, y no hace falta cola ni sondeo.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from django.db.models import QuerySet

from sunat_cpe.models import DocumentClass, Direction, ElectronicInvoice
from sunat_cpe.services.pdf import invoice_file_stem, render_invoice_pdf
from sunat_rhe.models import FeeReceipt
from sunat_rhe.services.pdf import receipt_file_stem, render_receipt_pdf

from ..models import DocumentExport, ExportSource

logger = logging.getLogger(__name__)

# Por descarga. Unos 4 ms por PDF y ~7 KB por comprobante en el zip: 2 000
# son unos 8 s y 14 MB, todavía cómodos en una petición.
MAX_DOCUMENTS = 2000

_PERIOD = re.compile(r"^\d{6}$")

CLASS_LABEL = {
    DocumentClass.INVOICE: "facturas",
    DocumentClass.CREDIT_NOTE: "notas de crédito",
    DocumentClass.DEBIT_NOTE: "notas de débito",
}
DIRECTION_LABEL = {Direction.ISSUED: "emitidas", Direction.RECEIVED: "recibidas"}
MESES = [
    "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic",
]


class InvalidSpec(ValueError):
    """El filtro no se entiende; el mensaje va tal cual al usuario."""


@dataclass(frozen=True)
class ExportSpec:
    source: str
    period_from: str
    period_to: str
    direction: str = ""
    document_classes: tuple[str, ...] = ()
    currency: str = ""

    def as_filters(self) -> dict[str, Any]:
        return {
            "source": self.source, "period_from": self.period_from,
            "period_to": self.period_to, "direction": self.direction,
            "document_classes": list(self.document_classes), "currency": self.currency,
        }

    def label(self) -> str:
        if self.source == ExportSource.RHE:
            what = "recibos por honorarios"
        else:
            classes = self.document_classes or tuple(CLASS_LABEL)
            what = " y ".join(CLASS_LABEL[c] for c in CLASS_LABEL if c in classes)
            if self.direction:
                what += f" {DIRECTION_LABEL[self.direction]}"
        rango = (
            _period_label(self.period_from) if self.period_from == self.period_to
            else f"{_period_label(self.period_from)} a {_period_label(self.period_to)}"
        )
        moneda = f" en {self.currency}" if self.currency else ""
        return f"{what}{moneda} · {rango}"


def _period_label(period: str) -> str:
    return f"{MESES[int(period[4:]) - 1]} {period[:4]}"


def parse_spec(data: Any) -> ExportSpec:
    """Lee y valida el filtro que manda el front."""
    if not isinstance(data, dict):
        raise InvalidSpec("Indica qué comprobantes descargar.")
    source = str(data.get("source") or "").strip()
    if source not in ExportSource.values:
        raise InvalidSpec("Indica si son facturas y notas (cpe) o recibos por honorarios (rhe).")
    period_from = str(data.get("period_from") or "").strip()
    period_to = str(data.get("period_to") or "").strip()
    for period in (period_from, period_to):
        if not _PERIOD.match(period) or not 1 <= int(period[4:]) <= 12:
            raise InvalidSpec("Indica el rango de meses como aaaamm.")
    if period_from > period_to:
        raise InvalidSpec("El mes inicial no puede ser posterior al final.")

    direction = str(data.get("direction") or "").strip()
    if direction and direction not in Direction.values:
        raise InvalidSpec("Dirección no válida.")
    raw_classes = data.get("document_classes") or []
    if not isinstance(raw_classes, list):
        raise InvalidSpec("Indica los tipos de comprobante como lista.")
    classes = tuple(dict.fromkeys(str(c) for c in raw_classes))
    unknown = [c for c in classes if c not in DocumentClass.values]
    if unknown:
        raise InvalidSpec("Tipo de comprobante no válido.")
    currency = str(data.get("currency") or "").strip().upper()[:3]
    if source == ExportSource.RHE:
        direction, classes, currency = "", (), ""
    return ExportSpec(
        source=source, period_from=period_from, period_to=period_to,
        direction=direction, document_classes=classes, currency=currency,
    )


def spec_from_export(export: DocumentExport) -> ExportSpec:
    f = export.filters
    return ExportSpec(
        source=export.source, period_from=f["period_from"], period_to=f["period_to"],
        direction=f.get("direction") or "",
        document_classes=tuple(f.get("document_classes") or ()),
        currency=f.get("currency") or "",
    )


def select_documents(account_ruc: str, spec: ExportSpec) -> QuerySet:
    """Los comprobantes del filtro, anulados y revertidos incluidos: también
    son documentos de la empresa y el índice los marca."""
    if spec.source == ExportSource.RHE:
        return (
            FeeReceipt.objects.for_account(account_ruc)
            .filter(period__gte=spec.period_from, period__lte=spec.period_to)
            .order_by("period", "issue_date", "series", "number")
        )
    queryset = (
        ElectronicInvoice.objects.for_account(account_ruc)
        .filter(period__gte=spec.period_from, period__lte=spec.period_to)
        .defer("raw")
        .order_by("period", "issue_date", "series", "number")
    )
    if spec.direction:
        queryset = queryset.filter(direction=spec.direction)
    if spec.document_classes:
        queryset = queryset.filter(document_class__in=spec.document_classes)
    if spec.currency:
        queryset = queryset.filter(currency=spec.currency)
    return queryset


# ------------------------------------------------------------------- zip
def _folder(period: str, kind: str) -> str:
    year, month = (period[:4], period[4:6]) if len(period) == 6 else ("0000", "00")
    return f"{year}/{month}/{kind}/"


@dataclass
class _Index:
    rows: list[list[str]] = field(default_factory=list)

    def add(self, *values: Any) -> None:
        self.rows.append(["" if v is None else str(v) for v in values])


def _write_invoices(archive: zipfile.ZipFile, queryset: QuerySet, index: _Index) -> None:
    for invoice in queryset.iterator(chunk_size=200):
        folder = _folder(invoice.period, invoice.document_class or "otros")
        stem = invoice_file_stem(invoice)
        files: list[str] = []
        if invoice.xml_content:
            archive.writestr(f"{folder}{stem}.xml", invoice.xml_content.encode("ISO-8859-1", "replace"))
            files.append(f"{stem}.xml")
            try:
                archive.writestr(f"{folder}{stem}.pdf", render_invoice_pdf(invoice))
                files.append(f"{stem}.pdf")
            except Exception:  # noqa: BLE001 — un XML raro no tumba la descarga
                logger.exception("PDF de %s no se pudo generar", stem)
                files.append("(PDF no generado)")
        else:
            files.append("(sin XML en SUNAT)")
        estado = invoice.status or ""
        if invoice.is_cancelled:
            estado = f"ANULADO · {estado}".strip(" ·")
        if invoice.is_rejected:
            estado = f"RECHAZADO · {estado}".strip(" ·")
        index.add(
            invoice.get_document_class_display(), invoice.full_number or f"{invoice.series}-{invoice.number}",
            invoice.issue_date, invoice.period, invoice.get_direction_display(),
            invoice.issuer_ruc, invoice.issuer_name, invoice.receiver_ruc, invoice.receiver_name,
            invoice.currency, invoice.total_amount, estado, invoice.references_document,
            " ".join(files),
        )


def _write_receipts(
    archive: zipfile.ZipFile, queryset: QuerySet, index: _Index, company_name: str,
) -> None:
    for receipt in queryset.iterator(chunk_size=200):
        folder = _folder(receipt.period, "recibo_honorarios")
        stem = receipt_file_stem(receipt)
        if receipt.file and receipt.file.name.lower().endswith(".pdf"):
            with receipt.file.open("rb") as stored:
                archive.writestr(f"{folder}{stem}.pdf", stored.read())
        else:
            archive.writestr(f"{folder}{stem}.pdf", render_receipt_pdf(receipt, company_name))
        estado = receipt.status or ""
        if receipt.is_reverted:
            estado = f"REVERTIDO · {estado}".strip(" ·")
        index.add(
            "Recibo por honorarios", receipt.full_number, receipt.issue_date, receipt.period,
            "Recibida", receipt.issuer_doc, receipt.issuer_name, receipt.account_ruc, company_name,
            receipt.currency, receipt.gross_amount, estado, "", f"{stem}.pdf",
        )


def build_zip(export: DocumentExport, company_name: str = "") -> bytes:
    spec = spec_from_export(export)
    queryset = select_documents(export.account_ruc, spec)
    index = _Index()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if spec.source == ExportSource.RHE:
            _write_receipts(archive, queryset, index, company_name)
        else:
            _write_invoices(archive, queryset, index)
        # Índice para Excel: BOM para que reconozca UTF-8 y respete las tildes.
        text = io.StringIO()
        writer = csv.writer(text, delimiter=";")
        writer.writerow([
            "tipo", "numero", "fecha", "periodo", "direccion", "emisor_ruc", "emisor",
            "receptor_ruc", "receptor", "moneda", "total", "estado", "modifica_a", "archivos",
        ])
        writer.writerows(index.rows)
        archive.writestr("indice.csv", "\ufeff" + text.getvalue())
        archive.writestr(
            "LEEME.txt",
            f"{export.label}\nEmpresa {export.account_ruc} · {export.document_count} comprobantes.\n\n"
            "Carpetas: año/mes/tipo. Cada factura o nota lleva su XML firmado por SUNAT (el "
            "documento con validez legal) y una representación impresa en PDF generada por "
            "EMPRESARIO. Los recibos por honorarios llevan el PDF entregado al registrarlos o "
            "una representación hecha con los datos de SUNAT.\n",
        )
    return buffer.getvalue()


def zip_name(export: DocumentExport) -> str:
    f = export.filters
    kind = "honorarios" if export.source == ExportSource.RHE else "comprobantes"
    return f"{kind}-{export.account_ruc}-{f.get('period_from', '')}-{f.get('period_to', '')}.zip"


__all__ = [
    "MAX_DOCUMENTS", "ExportSpec", "InvalidSpec", "build_zip", "parse_spec",
    "select_documents", "zip_name",
]
