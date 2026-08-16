"""Persistence for the executive finance analytics built on CPE + ITF.

The raw sources (``sunat_cpe.ElectronicInvoice``, ``sunat_itf.ItfRecord``)
stay untouched for audit; this app stores only derived artifacts:

* ``InvoiceExtract`` — normalized fields parsed from each comprobante's UBL
  XML, so listings and analytics never ship the full ``xml_content``.
* ``FinanceAlert`` — configurable, deduplicated alerts with a human-managed
  status.
* ``FinanceAiSummary`` — cached monthly AI summary, keyed by a fingerprint of
  the metrics it was generated from (regenerate only when the data changes).
* ``ManualEntry`` — ingresos y gastos registrados a mano; la sincronización
  nunca los toca y los totales mensuales los suman junto con lo automático.
* ``InvoiceOverride`` — correcciones manuales a comprobantes de SUNAT,
  guardadas aparte para que ninguna sincronización las chanque.
"""

from __future__ import annotations

from django.db import models

from core.models import BaseModel
from sunat_cpe.models import Direction, ElectronicInvoice


class ExtractStatus(models.TextChoices):
    DONE = "done", "Done"
    NO_XML = "no_xml", "No XML available"
    FAILED = "failed", "Failed"


class InvoiceExtract(BaseModel):
    """Normalized view of one comprobante's UBL XML."""

    invoice = models.OneToOneField(
        ElectronicInvoice, related_name="extract", on_delete=models.CASCADE
    )
    status = models.CharField(
        max_length=10, choices=ExtractStatus, default=ExtractStatus.DONE
    )
    fingerprint = models.CharField(
        max_length=80, blank=True,
        help_text="xml_sha256 + parser version; re-parse only when it changes.",
    )

    currency = models.CharField(max_length=3, blank=True)
    taxable_amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    igv_amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    total_amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )

    payment_form = models.CharField(
        max_length=20, blank=True, help_text="Contado / Credito según FormaPago."
    )
    installments = models.JSONField(
        default=list, blank=True,
        help_text='Cuotas: [{"amount": …, "due_date": "YYYY-MM-DD"}].',
    )
    detraction = models.JSONField(
        null=True, blank=True,
        help_text='{"percent": …, "amount": …} cuando el UBL declara detracción.',
    )

    # Solo Nota crédito/débito.
    reference_id = models.CharField(
        max_length=40, blank=True, help_text="Comprobante afectado (BillingReference)."
    )
    reference_reason_code = models.CharField(max_length=4, blank=True)
    reference_reason = models.CharField(
        max_length=255, blank=True, help_text="Motivo (DiscrepancyResponse)."
    )

    items = models.JSONField(
        default=list, blank=True,
        help_text='Líneas: [{"description": …, "quantity": …, "amount": …}].',
    )
    supplier_address = models.CharField(max_length=255, blank=True)
    customer_address = models.CharField(max_length=255, blank=True)
    notes = models.JSONField(default=list, blank=True, help_text="cbc:Note del XML.")

    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"extract {self.invoice_id}"


class ManualEntry(BaseModel):
    """Ingreso o gasto registrado a mano por la empresa.

    Vive en esta app y no en ``sunat_cpe`` a propósito: la sincronización
    hace ``update_or_create`` sobre ``ElectronicInvoice`` y jamás toca esta
    tabla, así que lo manual sobrevive a cualquier corrida. Los agregados
    mensuales lo suman junto con lo automático, siempre por moneda.

    ``direction`` reutiliza el vocabulario CPE: ``emitida`` = ingreso (se
    suma a Facturación), ``recibida`` = gasto (se suma a Comprobantes
    recibidos).
    """

    account_ruc = models.CharField(max_length=11, db_index=True)
    direction = models.CharField(max_length=10, choices=Direction, db_index=True)
    entry_date = models.DateField()
    period = models.CharField(
        max_length=6, db_index=True, help_text="aaaamm, derivado de entry_date."
    )
    description = models.CharField(max_length=200)
    counterparty = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=3, default="PEN")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "manual entries"
        ordering = ["-entry_date", "-created_at"]
        indexes = [
            models.Index(fields=["account_ruc", "direction", "period"]),
        ]

    def __str__(self) -> str:
        kind = "ingreso" if self.direction == Direction.ISSUED else "gasto"
        return f"[manual {kind}] {self.description}"


class InvoiceOverride(BaseModel):
    """Corrección manual de un comprobante extraído de SUNAT.

    El comprobante original queda intacto para auditoría y la corrección se
    guarda aparte: la sincronización re-escribe ``ElectronicInvoice`` pero
    nunca esta tabla, así que la edición no se chanca. Se aplica al leer:
    los agregados y el detalle usan el monto corregido cuando existe.
    """

    invoice = models.OneToOneField(
        ElectronicInvoice, related_name="override", on_delete=models.CASCADE
    )
    account_ruc = models.CharField(max_length=11, db_index=True)
    total_amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
        help_text="Reemplaza al total de SUNAT cuando no es nulo.",
    )
    counterparty = models.CharField(
        max_length=200, blank=True, help_text="Reemplaza a la contraparte mostrada."
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"override {self.invoice_id}"


class AlertSeverity(models.TextChoices):
    CRITICAL = "critical", "Crítica"
    HIGH = "high", "Alta"
    MEDIUM = "medium", "Media"
    INFO = "info", "Informativa"


class AlertStatus(models.TextChoices):
    NEW = "nueva", "Nueva"
    IN_REVIEW = "en_revision", "En revisión"
    RESOLVED = "resuelta", "Resuelta"
    DISMISSED = "descartada", "Descartada"


# Alerts worth acting on first. «info» stays out on purpose: it covers what is
# only pending classification, which is never presented as a breach.
PRIORITY_SEVERITIES = [AlertSeverity.CRITICAL, AlertSeverity.HIGH]

SEVERITY_RANK = {
    AlertSeverity.CRITICAL: 0,
    AlertSeverity.HIGH: 1,
    AlertSeverity.MEDIUM: 2,
    AlertSeverity.INFO: 3,
}


class FinanceAlertQuerySet(models.QuerySet):
    def open(self) -> "FinanceAlertQuerySet":
        return self.exclude(status__in=[AlertStatus.RESOLVED, AlertStatus.DISMISSED])

    def priority(self) -> "FinanceAlertQuerySet":
        return self.filter(severity__in=PRIORITY_SEVERITIES)


class FinanceAlert(BaseModel):
    """A configurable finance alert. Regeneration upserts by ``dedup_key`` so
    the same condition never produces duplicates, and the human-managed
    ``status`` survives recalculation."""

    account_ruc = models.CharField(max_length=11, db_index=True)
    dedup_key = models.CharField(max_length=160)
    alert_type = models.CharField(max_length=50)
    severity = models.CharField(max_length=10, choices=AlertSeverity)
    period = models.CharField(max_length=6, blank=True)
    title = models.CharField(max_length=200)
    explanation = models.TextField()
    amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    source = models.CharField(max_length=120, blank=True)
    recommendation = models.TextField(blank=True)
    status = models.CharField(
        max_length=15, choices=AlertStatus, default=AlertStatus.NEW
    )

    objects = FinanceAlertQuerySet.as_manager()

    class Meta:
        ordering = ["-period", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["account_ruc", "dedup_key"],
                name="unique_finance_alert_dedup",
            )
        ]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.title}"


class ActionStatus(models.TextChoices):
    """Estado de una acción del briefing. Lo mueve la persona, no el modelo."""

    SUGGESTED = "sugerida", "Sugerida"
    IN_PROGRESS = "en_curso", "En curso"
    DONE = "hecha", "Hecha"
    DISCARDED = "descartada", "Descartada"


class FinanceAiSummary(BaseModel):
    """Cached monthly executive briefing (metrics in → briefing out, never
    recomputed unless the underlying metrics change).

    Three blocks feed the UI: ``key_changes`` (qué cambió), ``attention``
    (qué mirar) y ``actions`` (qué hacer, con responsable, fecha y estado).
    El estado de cada acción lo gestiona la persona y sobrevive a las
    regeneraciones: se transfiere por ``id`` de acción.
    """

    account_ruc = models.CharField(max_length=11, db_index=True)
    period = models.CharField(max_length=6, db_index=True)
    fingerprint = models.CharField(max_length=64)
    model_name = models.CharField(max_length=60, blank=True)
    summary = models.TextField(help_text="Lectura ejecutiva, máximo 60 palabras.")
    key_changes = models.JSONField(
        default=list, blank=True, help_text="Cambios clave: hasta 3 frases."
    )
    attention = models.JSONField(
        default=list, blank=True,
        help_text='Requieren atención: [{"title": …, "detail": …}], hasta 3.',
    )
    actions = models.JSONField(
        default=list, blank=True,
        help_text=(
            'Acciones sugeridas: [{"id", "action", "owner", "due_date", '
            '"status", "why"}], hasta 3.'
        ),
    )

    class Meta:
        verbose_name_plural = "finance AI summaries"
        ordering = ["-period", "-created_at"]

    def __str__(self) -> str:
        return f"AI summary {self.period}"

    def set_action_status(self, action_id: str, status: str) -> bool:
        """Mueve el estado de una acción. Devuelve False si no existe."""
        for action in self.actions:
            if action.get("id") == action_id:
                action["status"] = status
                self.save(update_fields=["actions", "updated_at"])
                return True
        return False
