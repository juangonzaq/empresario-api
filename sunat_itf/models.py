"""Persistence for SUNAT's ITF report (Consulta de ITF, menu 13.6.1.1.1).

The report has three sections — accumulated movements, error contra-entries, and
exemptions/agreements — with overlapping but not identical columns. One flexible
model holds all three: the shared analytic columns are promoted to fields, and
every section-specific column is preserved in ``extra`` (plus the whole raw row
in ``raw`` so nothing scraped is ever lost).
"""

from __future__ import annotations

from django.db import models

from core.models import BaseModel


class ItfSection(models.TextChoices):
    """The three tables the Consulta de ITF renders."""

    ACCUMULATED = "accumulated", "Movimientos Acumulados"
    ERROR_REVERSAL = "error_reversal", "Movimientos de Contra Asientos por Error"
    EXEMPTION = "exemption", "Exoneración, Convenios e Inafectación"


class ItfRecordQuerySet(models.QuerySet):
    def for_taxpayer(self, taxpayer_id: str) -> "ItfRecordQuerySet":
        return self.filter(taxpayer_id=taxpayer_id)

    def in_section(self, section: str) -> "ItfRecordQuerySet":
        return self.filter(section=section)

    def for_period(self, period: str) -> "ItfRecordQuerySet":
        return self.filter(period=period)


class ItfRecord(BaseModel):
    """One row of the Consulta de ITF report."""

    taxpayer_id = models.CharField(
        "RUC contribuyente", max_length=11, db_index=True,
        help_text="RUC whose ITF is being consulted (form field numdocdec).",
    )
    section = models.CharField(max_length=20, choices=ItfSection, db_index=True)
    period = models.CharField(
        max_length=6, db_index=True, help_text="Tax period yyyymm (Período)."
    )

    declarant_ruc = models.CharField(
        max_length=11, blank=True, help_text="RUC of the bank/declarant.",
    )
    declarant_name = models.CharField(max_length=200, blank=True)
    doc_type = models.CharField(max_length=2, blank=True, help_text="Tipo Doc.")

    kind = models.CharField(max_length=40, blank=True, help_text="Tipo (e.g. Afecta).")
    movement = models.CharField(
        max_length=60, blank=True, help_text="Movimiento (e.g. Usando cuenta)."
    )
    modality = models.CharField(max_length=60, blank=True, help_text="Modalidad.")
    operation_code = models.CharField(max_length=6, blank=True, help_text="Cod. Oper.")

    base_amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True, help_text="Base."
    )
    tax = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True, help_text="Impuesto."
    )

    # Section-specific columns (Per. Oper., Cuenta, Inciso, Fecha, Hora, ...) and the
    # verbatim row, kept so new columns can be surfaced without re-scraping.
    extra = models.JSONField(default=dict, blank=True)
    raw = models.JSONField(default=list, blank=True)

    objects = ItfRecordQuerySet.as_manager()

    class Meta:
        ordering = ["-period", "declarant_ruc", "operation_code"]
        indexes = [
            models.Index(fields=["taxpayer_id", "section", "period"]),
            models.Index(fields=["-period"]),
        ]

    def __str__(self) -> str:
        return (
            f"[{self.taxpayer_id}] {self.period} {self.get_section_display()} "
            f"{self.declarant_name[:30]} {self.tax or ''}"
        )
