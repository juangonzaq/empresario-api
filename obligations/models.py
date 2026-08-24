"""Compliance / obligations data model.

The design rule: **existing models are the source of truth; this module only
interprets them.** Nothing here re-stores an invoice, an employee or a
declaration — obligations point at that data (or are inferred from it) and add
a thin layer of judgement: does this rule apply to this company, is it met, who
owns it, what proves it.

Two tenancy tiers:

* The **catalog** (:class:`ComplianceDomain`, :class:`ComplianceRule`) is global
  and versioned — the same rules describe every company. Rules never hold
  executable code; applicability is a declarative JSON expression and the hard
  logic lives behind a named ``evaluator_key`` in a controlled registry.
* The **per-company** rows (:class:`CompanyObligation` and below) are scoped by
  ``account_ruc`` and resolved from the caller's organization, never a param.

Statuses live on separate axes (see ``obligations.enums``) so a single field
never has to mean both "not due yet" and "no evidence".
"""

from __future__ import annotations

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import BaseModel

from . import enums


class ComplianceDomain(BaseModel):
    """A compliance area (tax, labor, corporate, …). Just a catalog bucket."""

    code = models.SlugField("código", max_length=40, unique=True)
    name = models.CharField("nombre", max_length=80)
    description = models.CharField("descripción", max_length=240, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name = "dominio de cumplimiento"
        verbose_name_plural = "dominios de cumplimiento"

    def __str__(self) -> str:
        return f"{self.code} · {self.name}"


class ComplianceRule(BaseModel):
    """A single obligation or control, described once for every company.

    ``applicability`` is a declarative expression (``{"all": [{field, operator,
    value}]}``) evaluated against a read-only company context — never Python in
    the database. When applicability or the verdict needs real logic, a named
    ``evaluator_key`` resolves to a function in ``services.evaluators``.
    """

    domain = models.ForeignKey(ComplianceDomain, related_name="rules", on_delete=models.PROTECT)
    code = models.SlugField("código", max_length=60, unique=True)
    title = models.CharField("título", max_length=160)
    summary = models.CharField("resumen", max_length=280, blank=True)

    obligation_type = models.CharField(max_length=20, choices=enums.ObligationType,
                                       default=enums.ObligationType.LEGAL)
    default_severity = models.CharField(max_length=10, choices=enums.Severity,
                                        default=enums.Severity.MEDIUM)
    frequency = models.CharField(max_length=15, choices=enums.Frequency,
                                 default=enums.Frequency.ONE_TIME)
    # Peso para el score ponderado (una obligación crítica pesa más que un
    # recordatorio). Si es 0, se usa el peso derivado de la severidad.
    weight = models.PositiveSmallIntegerField(default=0)

    legal_reference = models.CharField("base legal", max_length=200, blank=True)
    source_name = models.CharField(max_length=120, blank=True)
    source_url = models.URLField(max_length=400, blank=True)

    applicability = models.JSONField(
        "condiciones de aplicabilidad", default=dict, blank=True,
        help_text='Expresión declarativa: {"all": [{"field","operator","value"}]}. '
                  'Vacío = aplica siempre.',
    )
    # Clave del evaluador en el registro controlado (services.evaluators). Vacío
    # = solo aplicabilidad declarativa, sin veredicto automático de cumplimiento.
    evaluator_key = models.SlugField(max_length=60, blank=True)

    required_evidence = models.JSONField(default=list, blank=True)
    remediation_steps = models.JSONField(default=list, blank=True)

    version = models.PositiveIntegerField(default=1)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["domain__sort_order", "sort_order", "code"]
        verbose_name = "regla de cumplimiento"
        verbose_name_plural = "reglas de cumplimiento"

    def __str__(self) -> str:
        return f"{self.code} · {self.title}"

    @property
    def effective_weight(self) -> int:
        return self.weight or enums.SEVERITY_WEIGHT.get(self.default_severity, 2)


class CompanyObligation(BaseModel):
    """A rule as it lands on one company: does it apply, is it met, who owns it.

    One row per (company, rule); the engine upserts it. Time-derived states
    (overdue / due soon) are computed from ``due_date`` at read time, never
    stored here.
    """

    account_ruc = models.CharField(max_length=11, db_index=True)
    rule = models.ForeignKey(ComplianceRule, related_name="company_obligations", on_delete=models.CASCADE)

    applicability_status = models.CharField(max_length=15, choices=enums.ApplicabilityStatus,
                                            default=enums.ApplicabilityStatus.UNKNOWN)
    compliance_status = models.CharField(max_length=15, choices=enums.ComplianceStatus,
                                         default=enums.ComplianceStatus.UNKNOWN)
    workflow_status = models.CharField(max_length=15, choices=enums.WorkflowStatus,
                                       default=enums.WorkflowStatus.NOT_STARTED)
    verification_status = models.CharField(max_length=15, choices=enums.VerificationStatus,
                                           default=enums.VerificationStatus.UNVERIFIED)
    severity = models.CharField(max_length=10, choices=enums.Severity, default=enums.Severity.MEDIUM)

    due_date = models.DateField(null=True, blank=True, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="owned_obligations")

    applicability_reason = models.CharField(max_length=280, blank=True)
    current_assessment = models.CharField(max_length=400, blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    last_evaluated_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["account_ruc", "rule__domain__sort_order", "rule__sort_order"]
        verbose_name = "obligación de la empresa"
        verbose_name_plural = "obligaciones de la empresa"
        constraints = [
            models.UniqueConstraint(fields=["account_ruc", "rule"], name="unique_company_rule"),
        ]
        indexes = [
            models.Index(fields=["account_ruc", "compliance_status", "due_date"]),
            models.Index(fields=["account_ruc", "workflow_status", "owner"]),
            models.Index(fields=["account_ruc", "last_evaluated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.account_ruc} · {self.rule.code} · {self.compliance_status}"


class ObligationAssessment(BaseModel):
    """One evaluation of an obligation. The audit trail that lets the screen
    explain *why* something is marked the way it is."""

    company_obligation = models.ForeignKey(CompanyObligation, related_name="assessments",
                                           on_delete=models.CASCADE)
    rule_version = models.PositiveIntegerField(default=1)
    applicability_status = models.CharField(max_length=15, choices=enums.ApplicabilityStatus,
                                            default=enums.ApplicabilityStatus.UNKNOWN)
    compliance_status = models.CharField(max_length=15, choices=enums.ComplianceStatus,
                                         default=enums.ComplianceStatus.UNKNOWN)
    verification_status = models.CharField(max_length=15, choices=enums.VerificationStatus,
                                           default=enums.VerificationStatus.UNVERIFIED)
    evaluation_source = models.CharField(max_length=15, choices=enums.EvaluationSource,
                                         default=enums.EvaluationSource.RULE_ENGINE)
    reason = models.CharField(max_length=400, blank=True)
    # El snapshot captura el contexto tal cual; DjangoJSONEncoder tolera fechas,
    # Decimals y UUIDs que puedan colarse desde los modelos fuente.
    input_snapshot = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    evaluated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "evaluación de obligación"
        verbose_name_plural = "evaluaciones de obligación"
        indexes = [models.Index(fields=["company_obligation", "created_at"])]

    def __str__(self) -> str:
        return f"{self.company_obligation_id} · {self.compliance_status} · {self.created_at:%Y-%m-%d}"


class ObligationEvidence(BaseModel):
    """Evidence that an obligation is met. Points at data that already exists
    (a declaration, a receipt, an uploaded file) — it does not duplicate it.

    There is no single generic Document model in the platform, so the reference
    is a soft pointer: ``reference`` = ``{"model": "reconciliation.DeclaredSummary",
    "id": "...", "period": "202607"}`` (or a URL / label). This keeps evidence
    honest without forcing a premature attachments table."""

    company_obligation = models.ForeignKey(CompanyObligation, related_name="evidence",
                                           on_delete=models.CASCADE)
    evidence_type = models.CharField(max_length=15, choices=enums.EvidenceType,
                                     default=enums.EvidenceType.DOCUMENT)
    verification_status = models.CharField(max_length=15, choices=enums.VerificationStatus,
                                           default=enums.VerificationStatus.SELF_REPORTED)
    label = models.CharField(max_length=200, blank=True)
    reference = models.JSONField(default=dict, blank=True)
    url = models.URLField(max_length=500, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=400, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "evidencia de obligación"
        verbose_name_plural = "evidencias de obligación"

    def __str__(self) -> str:
        return f"{self.company_obligation_id} · {self.evidence_type}"


class ComplianceAction(BaseModel):
    """A task on an obligation. The platform has no generic task model, so this
    one carries owner + due date + status the way an action plan needs."""

    company_obligation = models.ForeignKey(CompanyObligation, related_name="actions",
                                           on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=600, blank=True)
    status = models.CharField(max_length=15, choices=enums.ActionStatus,
                              default=enums.ActionStatus.SUGGESTED)
    priority = models.CharField(max_length=10, choices=enums.ActionPriority,
                                default=enums.ActionPriority.MEDIUM)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="compliance_actions")
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "acción de cumplimiento"
        verbose_name_plural = "acciones de cumplimiento"

    def __str__(self) -> str:
        return f"{self.title} · {self.status}"


class ComplianceSnapshot(BaseModel):
    """A daily photo of a company's compliance, for the trend chart. One row per
    (company, day); re-running the same day overwrites it."""

    account_ruc = models.CharField(max_length=11, db_index=True)
    snapshot_date = models.DateField(db_index=True)
    overall_score = models.PositiveSmallIntegerField(default=0)
    applicable_count = models.PositiveSmallIntegerField(default=0)
    compliant_count = models.PositiveSmallIntegerField(default=0)
    non_compliant_count = models.PositiveSmallIntegerField(default=0)
    unverified_count = models.PositiveSmallIntegerField(default=0)
    overdue_count = models.PositiveSmallIntegerField(default=0)
    domain_metrics = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-snapshot_date"]
        verbose_name = "foto de cumplimiento"
        verbose_name_plural = "fotos de cumplimiento"
        constraints = [
            models.UniqueConstraint(fields=["account_ruc", "snapshot_date"],
                                    name="unique_snapshot_per_day"),
        ]
        indexes = [models.Index(fields=["snapshot_date", "account_ruc"])]

    def __str__(self) -> str:
        return f"{self.account_ruc} · {self.snapshot_date} · {self.overall_score}"
