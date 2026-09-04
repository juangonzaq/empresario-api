"""Persistence for SUNAT's electronic comprobantes (Consultar Factura y Nota).

Covers all seven ``tipoConsulta`` flavours — facturas / Nota crédito / notas
de débito, each emitida or recibida, plus rejected facturas — for a single SOL
account. One row per physical comprobante (keyed by its issuer + series + number
+ type); the daily sync re-queries and updates it, recording any balance-relevant
state change in ``state_history`` so a document that flips state is never silently
lost.
"""

from __future__ import annotations

from pathlib import Path

from django.db import models

from core.archive import document_path
from core.models import BaseModel


class Direction(models.TextChoices):
    ISSUED = "emitida", "Emitida"
    RECEIVED = "recibida", "Recibida"


class DocumentClass(models.TextChoices):
    INVOICE = "factura", "Factura"
    CREDIT_NOTE = "nota_credito", "Nota de crédito"
    DEBIT_NOTE = "nota_debito", "Nota de débito"


class CpeQuerySet(models.QuerySet):
    def for_account(self, account_ruc: str) -> "CpeQuerySet":
        return self.filter(account_ruc=account_ruc)

    # Kept for continuity with the other SUNAT modules' vocabulary.
    def for_taxpayer(self, account_ruc: str) -> "CpeQuerySet":
        return self.for_account(account_ruc)

    def issued(self) -> "CpeQuerySet":
        return self.filter(direction=Direction.ISSUED)

    def received(self) -> "CpeQuerySet":
        return self.filter(direction=Direction.RECEIVED)

    def with_xml(self) -> "CpeQuerySet":
        return self.exclude(xml_content="")

    def missing_xml(self) -> "CpeQuerySet":
        return self.filter(xml_content="", can_download=True)

    def for_period(self, period: str) -> "CpeQuerySet":
        return self.filter(period=period)


def xml_file_path(instance: "ElectronicInvoice", filename: str) -> str:
    """comprobantes/<ruc>/<año>/<mes>/<factura|nota_credito|nota_debito>/<serie-número>-<uuid>.xml"""
    return document_path(
        account_ruc=instance.account_ruc,
        period=instance.period,
        kind=instance.document_class or "otros",
        code=f"{instance.series}-{instance.number}",
        pk=instance.pk,
        extension=Path(filename).suffix or ".xml",
    )


class ElectronicInvoice(BaseModel):
    """An electronic comprobante linked to the SOL account (emitido or recibido)."""

    account_ruc = models.CharField(
        "RUC de la cuenta", max_length=11, db_index=True,
        help_text="RUC of the logged-in SOL account this record belongs to.",
    )
    direction = models.CharField(max_length=10, choices=Direction, db_index=True)
    document_class = models.CharField(max_length=15, choices=DocumentClass, db_index=True)

    # SUNAT codes. document_type is tipoCPE (10 FE, 21 NC, ...); download_code is
    # codFactura, the value the XML download form expects as 'tipo'.
    document_type = models.CharField(max_length=3, help_text="tipoCPE.")
    cpe_code = models.CharField(max_length=2, blank=True, help_text="codCpe (01/07/08).")
    download_code = models.CharField(max_length=3, blank=True, help_text="codFactura.")
    tipo_consulta = models.CharField(
        max_length=2, blank=True, help_text="Last tipoConsulta that surfaced this row.",
    )

    issuer_ruc = models.CharField(max_length=15, db_index=True, help_text="nroRucEmisor.")
    issuer_name = models.CharField(max_length=200, blank=True)
    receiver_doc_type = models.CharField(max_length=2, blank=True)
    receiver_ruc = models.CharField(max_length=15, blank=True, db_index=True)
    receiver_name = models.CharField(max_length=200, blank=True)

    series = models.CharField(max_length=8)
    number = models.CharField(max_length=12)
    full_number = models.CharField(max_length=30, blank=True)

    issue_date = models.DateField(null=True, blank=True)
    period = models.CharField(max_length=6, db_index=True)

    currency = models.CharField(max_length=3, blank=True)
    currency_symbol = models.CharField(max_length=5, blank=True)
    total_amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )

    status = models.CharField(max_length=40, blank=True, help_text="estadoDesc.")
    is_cancelled = models.BooleanField(default=False, help_text="ind_anulado.")
    is_rejected = models.BooleanField(default=False)
    reject_date = models.CharField(max_length=20, blank=True)

    # The comprobante this one is issued against (comprobantePorElQueSeEmite): a
    # nota de crédito/débito points at the factura it modifies, so balances are
    # obtained by cross-referencing rather than by re-checking each document.
    references_document = models.CharField(
        max_length=60, blank=True, db_index=True,
        help_text="Original comprobante a NC/ND is issued against.",
    )

    xml_id = models.CharField(max_length=20, blank=True)
    can_download = models.BooleanField(default=False)

    xml_content = models.TextField(blank=True, help_text="Signed UBL XML text.")
    xml_filename = models.CharField(max_length=120, blank=True)
    xml_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    xml_downloaded_at = models.DateTimeField(null=True, blank=True)
    # The same XML as a file on disk, byte-for-byte as SUNAT served it, filed
    # by company and period (core.archive). Written through core.archive.store.
    xml_file = models.FileField(
        "archivo XML", upload_to=xml_file_path, max_length=255, blank=True,
    )

    # When the daily query last returned this comprobante.
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)

    raw = models.JSONField(default=dict, blank=True)

    objects = CpeQuerySet.as_manager()

    class Meta:
        ordering = ["-issue_date", "-number"]
        indexes = [
            models.Index(fields=["account_ruc", "direction", "period"]),
            models.Index(fields=["-issue_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["account_ruc", "issuer_ruc", "document_type", "series", "number"],
                name="unique_cpe_per_account",
            )
        ]

    def __str__(self) -> str:
        label = self.full_number or f"{self.series}-{self.number}"
        return f"[{self.get_direction_display()}] {label}"
