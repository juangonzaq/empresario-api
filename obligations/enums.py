"""Enums for the compliance/obligations module.

Statuses are kept as *separate* axes on purpose. Mixing "pending", "overdue",
"verified" and "partial" into a single field is what makes compliance screens
lie: an obligation can be COMPLIANT yet UNVERIFIED, or IN_PROGRESS and not yet
due. Each axis answers one question, and time-derived states (OVERDUE, DUE_SOON)
are computed from ``due_date`` — never stored.
"""

from __future__ import annotations

from django.db import models


class ObligationType(models.TextChoices):
    LEGAL = "legal", "Obligación legal"
    CONTRACTUAL = "contractual", "Obligación contractual"
    PREVENTIVE_CONTROL = "preventive_control", "Control preventivo"
    RECOMMENDATION = "recommendation", "Recomendación"


class Frequency(models.TextChoices):
    ONE_TIME = "one_time", "Única vez"
    MONTHLY = "monthly", "Mensual"
    QUARTERLY = "quarterly", "Trimestral"
    ANNUAL = "annual", "Anual"
    EVENT_DRIVEN = "event_driven", "Por evento"
    CONTINUOUS = "continuous", "Permanente"


class ApplicabilityStatus(models.TextChoices):
    APPLICABLE = "applicable", "Aplica"
    NOT_APPLICABLE = "not_applicable", "No aplica"
    UNKNOWN = "unknown", "Por determinar"


class ComplianceStatus(models.TextChoices):
    COMPLIANT = "compliant", "Cumple"
    NON_COMPLIANT = "non_compliant", "No cumple"
    UNKNOWN = "unknown", "Sin determinar"


class WorkflowStatus(models.TextChoices):
    NOT_STARTED = "not_started", "Sin empezar"
    IN_PROGRESS = "in_progress", "En proceso"
    BLOCKED = "blocked", "Bloqueada"
    COMPLETED = "completed", "Completada"


class VerificationStatus(models.TextChoices):
    VERIFIED = "verified", "Verificada"
    SELF_REPORTED = "self_reported", "Declarada por la empresa"
    INFERRED = "inferred", "Inferida de tus datos"
    UNVERIFIED = "unverified", "Sin evidencia"


class Severity(models.TextChoices):
    CRITICAL = "critical", "Crítica"
    HIGH = "high", "Alta"
    MEDIUM = "medium", "Media"
    LOW = "low", "Baja"


# Peso relativo para ordenar y para el score ponderado. Menor número = más grave.
SEVERITY_WEIGHT: dict[str, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
}


class EvaluationSource(models.TextChoices):
    """De dónde salió el veredicto de una evaluación."""

    RULE_ENGINE = "rule_engine", "Motor de reglas"
    EVIDENCE = "evidence", "Evidencia registrada"
    USER = "user", "Declarado por el usuario"


class EvidenceType(models.TextChoices):
    DECLARATION = "declaration", "Declaración"
    RECEIPT = "receipt", "Comprobante"
    CONTRACT = "contract", "Contrato"
    REGISTRATION = "registration", "Inscripción / registro"
    CERTIFICATE = "certificate", "Constancia / certificado"
    DOCUMENT = "document", "Documento"
    OTHER = "other", "Otro"


class ActionStatus(models.TextChoices):
    SUGGESTED = "suggested", "Sugerida"
    IN_PROGRESS = "in_progress", "En curso"
    DONE = "done", "Hecha"
    DISMISSED = "dismissed", "Descartada"


class ActionPriority(models.TextChoices):
    HIGH = "high", "Alta"
    MEDIUM = "medium", "Media"
    LOW = "low", "Baja"
