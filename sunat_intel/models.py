"""Persistence for the AI-assisted analysis of SUNAT communications.

Three concerns are kept strictly separate, mirroring the product rules:

* **Lectura** lives on ``sunat_mailbox.Message`` (``is_read`` / ``reviewed_at``).
* **Prioridad** is produced per message by the analysis (``MessageAnalysis``).
* **Gestión** is tracked per case (``Case.status``), driven by people.

An unread message is therefore never automatically urgent.
"""

from __future__ import annotations

from django.db import models

from core.models import BaseModel
from sunat_mailbox.models import Message


class AnalysisStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class Priority(models.TextChoices):
    CRITICAL = "critical", "Crítica"
    HIGH = "high", "Alta"
    MEDIUM = "medium", "Media"
    INFORMATIONAL = "informational", "Informativa"


class Confidence(models.TextChoices):
    HIGH = "high", "Alta"
    MEDIUM = "medium", "Media"
    LOW = "low", "Baja"


class MessageAnalysis(BaseModel):
    """The stored result of analyzing one mailbox message with the LLM.

    Results are cached: a message is only re-analyzed when its content, its
    attachments or the methodology version change (``fingerprint``).
    """

    message = models.OneToOneField(
        Message, related_name="analysis", on_delete=models.CASCADE
    )
    status = models.CharField(
        max_length=10, choices=AnalysisStatus, default=AnalysisStatus.PENDING
    )
    fingerprint = models.CharField(
        max_length=64, blank=True,
        help_text="SHA-256 over message content + attachment text + methodology "
                  "version + model. Reprocess only when it changes.",
    )
    model_name = models.CharField(max_length=60, blank=True)

    comm_type = models.CharField(max_length=120, blank=True)
    priority = models.CharField(
        max_length=15, choices=Priority, default=Priority.INFORMATIONAL
    )
    requires_action = models.BooleanField(default=False)
    summary = models.TextField(blank=True)
    why_it_matters = models.TextField(blank=True)
    next_action = models.TextField(blank=True)

    tribute = models.CharField(max_length=60, blank=True)
    tax_period = models.CharField(max_length=7, blank=True)
    references = models.JSONField(
        default=list, blank=True,
        help_text="Document numbers found in the evidence (resolución, orden de "
                  "pago, expediente…), used to group messages into cases.",
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Only stored when the amount appears verbatim in a source.",
    )
    amount_source = models.CharField(max_length=255, blank=True)
    legal_deadline = models.DateField(
        null=True, blank=True,
        help_text="Only stored when a legal deadline is evidenced in a source. "
                  "Never derived from the mailbox expires_at.",
    )
    deadline_source = models.CharField(max_length=255, blank=True)

    missing_info = models.JSONField(default=list, blank=True)
    confidence = models.CharField(
        max_length=10, choices=Confidence, default=Confidence.LOW
    )
    sources = models.JSONField(
        default=list, blank=True,
        help_text='E.g. ["asunto", "adjunto:constancia.pdf"].',
    )

    raw_response = models.JSONField(
        null=True, blank=True, help_text="Verbatim LLM output, kept for audit."
    )
    error = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "message analyses"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_priority_display()} · {self.message.subject[:60]}"


class CaseStatus(models.TextChoices):
    """Gestión — driven by people, never auto-advanced by the AI."""

    UNREVIEWED = "sin_revisar", "Sin revisar"
    IN_ANALYSIS = "en_analisis", "En análisis"
    ACTION_REQUIRED = "requiere_accion", "Requiere acción"
    DELEGATED = "delegado", "Delegado"
    WAITING_THIRD_PARTY = "esperando_tercero", "Esperando tercero"
    RESOLVED = "resuelto", "Resuelto"
    DISMISSED = "descartado", "Descartado"


OPEN_STATUSES = (
    CaseStatus.UNREVIEWED, CaseStatus.IN_ANALYSIS, CaseStatus.ACTION_REQUIRED,
    CaseStatus.DELEGATED, CaseStatus.WAITING_THIRD_PARTY,
)


class CaseQuerySet(models.QuerySet):
    def open(self) -> "CaseQuerySet":
        return self.filter(status__in=OPEN_STATUSES)


class Case(BaseModel):
    """A business matter grouping every related SUNAT communication.

    E.g. an orden de pago, its ejecución coactiva and the resolución de
    conclusión are one case with three related messages, not three alerts.
    """

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    group_key = models.CharField(
        max_length=255,
        help_text="Canonical reference that binds the related messages.",
    )

    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    why_it_matters = models.TextField(blank=True)
    risk = models.CharField(
        max_length=15, choices=Priority, default=Priority.INFORMATIONAL,
        help_text="Follows the latest communication: a conclusión can lower it.",
    )
    status = models.CharField(
        max_length=20, choices=CaseStatus, default=CaseStatus.UNREVIEWED
    )
    requires_decision = models.BooleanField(default=False)
    responsible = models.CharField(max_length=120, blank=True)
    next_action = models.TextField(blank=True)

    exposure_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    exposure_source = models.CharField(max_length=255, blank=True)
    deadline = models.DateField(null=True, blank=True)
    deadline_source = models.CharField(max_length=255, blank=True)

    tribute = models.CharField(max_length=60, blank=True)
    tax_period = models.CharField(max_length=7, blank=True)
    confidence = models.CharField(
        max_length=10, choices=Confidence, default=Confidence.LOW
    )

    messages = models.ManyToManyField(Message, related_name="cases", blank=True)

    objects = CaseQuerySet.as_manager()

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "group_key"],
                name="unique_case_group_per_taxpayer",
            )
        ]

    def __str__(self) -> str:
        return f"[{self.get_risk_display()}] {self.title}"


class ChatRole(models.TextChoices):
    USER = "user", "Usuario"
    ASSISTANT = "assistant", "VIGÍA"


class VigiaMessage(BaseModel):
    """Persisted chat history of «Pregúntale a VIGÍA».

    Doubles as the audit log of who consulted the assistant and what it
    answered. Recent turns are replayed to the model so follow-up questions
    keep their context.
    """

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    role = models.CharField(max_length=10, choices=ChatRole)
    content = models.TextField()
    sources = models.JSONField(default=list, blank=True)
    has_sufficient_info = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:60]}"


class CaseEvent(BaseModel):
    """Audit trail: who consulted, changed or closed a case, and what the
    system did on its behalf."""

    case = models.ForeignKey(Case, related_name="events", on_delete=models.CASCADE)
    actor = models.CharField(max_length=120, default="sistema")
    kind = models.CharField(max_length=40)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind} · {self.case.title[:40]}"
