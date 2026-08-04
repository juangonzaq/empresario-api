"""Persistence models for the SUNAT compliance profile (perfil de cumplimiento)."""

from __future__ import annotations

from django.db import models

from core.models import BaseModel


class Rating(models.TextChoices):
    """The five calificaciones defined by Decreto Legislativo 1535."""

    VERY_HIGH = "A", "Nivel de cumplimiento muy alto"
    HIGH = "B", "Nivel de cumplimiento alto"
    MEDIUM = "C", "Nivel de cumplimiento medio"
    LOW = "D", "Nivel de cumplimiento bajo"
    VERY_LOW = "E", "Nivel de cumplimiento muy bajo"


class ComplianceRatingQuerySet(models.QuerySet):
    def for_taxpayer(self, taxpayer_id: str) -> "ComplianceRatingQuerySet":
        return self.filter(taxpayer_id=taxpayer_id)

    def current(self) -> "ComplianceRatingQuerySet":
        return self.filter(is_current=True)


class ComplianceRating(BaseModel):
    """One quarterly calificación of a taxpayer's compliance profile.

    Rows come from SUNAT's ``cabecera`` (the vigente quarter) and ``historico``
    endpoints; the reasons behind the score live in ``detail_payload`` and the
    related :class:`ComplianceVariable` rows.
    """

    taxpayer_id = models.CharField(
        "RUC", max_length=11, db_index=True,
        help_text="Taxpayer identification number the rating belongs to.",
    )
    period = models.PositiveIntegerField(
        help_text="Calification quarter, SUNAT's trimCal (e.g. 202602)."
    )
    execution_period = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Month the calification was computed, SUNAT's perEjec.",
    )
    preliminary_category = models.CharField(
        max_length=2, blank=True, help_text="SUNAT's codCatPrel."
    )
    rating = models.CharField(
        max_length=2, choices=Rating, blank=True,
        help_text="Final calificación, SUNAT's codVarFinal (A best to E worst).",
    )
    evaluation_start = models.PositiveIntegerField(
        null=True, blank=True, help_text="First evaluated month, SUNAT's perEvalIni."
    )
    evaluation_end = models.PositiveIntegerField(
        null=True, blank=True, help_text="Last evaluated month, SUNAT's perEvalFin."
    )
    data_location_code = models.SmallIntegerField(
        null=True, blank=True,
        help_text="SUNAT's codUbicaDatos, stored verbatim; semantics undocumented.",
    )
    loaded_at = models.DateTimeField(
        null=True, blank=True, help_text="When SUNAT loaded the data (fecCarga)."
    )
    is_current = models.BooleanField(
        default=False,
        help_text="True for the quarter SUNAT's cabecera endpoint reports as vigente.",
    )

    # Raw SUNAT payloads, kept so new fields can be backfilled without re-scraping.
    header_payload = models.JSONField(default=dict, blank=True)
    detail_payload = models.JSONField(null=True, blank=True)
    detail_fetched_at = models.DateTimeField(null=True, blank=True)

    objects = ComplianceRatingQuerySet.as_manager()

    class Meta:
        ordering = ["-period"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "period"],
                name="unique_rating_per_taxpayer_period",
            )
        ]
        indexes = [models.Index(fields=["taxpayer_id", "is_current"])]

    def __str__(self) -> str:
        return f"[{self.taxpayer_id}] {self.period}: {self.rating or '?'}"


class VariableType(models.TextChoices):
    """SUNAT's codTipoVariable discriminator."""

    WEIGHTING = "P", "Ponderación"
    LINKAGE = "V", "Vinculación"
    DIRECT = "D", "Calificación directa"


class ComplianceVariableQuerySet(models.QuerySet):
    def of_type(self, variable_type: str) -> "ComplianceVariableQuerySet":
        return self.filter(variable_type=variable_type)


class ComplianceVariable(BaseModel):
    """One incumplimiento (or linkage/direct-rating cause) behind a calificación."""

    rating = models.ForeignKey(
        ComplianceRating, related_name="variables", on_delete=models.CASCADE
    )
    variable_type = models.CharField(
        max_length=2, choices=VariableType, db_index=True
    )
    type_label = models.CharField(
        max_length=60, blank=True, help_text="SUNAT's desTipoVariable."
    )
    code = models.CharField(
        max_length=20, blank=True, help_text="SUNAT's codVariable (e.g. v0615)."
    )
    description = models.TextField(blank=True)
    severity = models.CharField(
        max_length=30, blank=True,
        help_text="SUNAT's desGravedad: Leve, Grave or Muy grave.",
    )
    entity_name = models.CharField(
        max_length=80, blank=True, help_text="SUNAT's nomEntidad backing table."
    )
    is_complete = models.BooleanField(default=True, help_text="SUNAT's indCompletado.")
    is_multipage = models.BooleanField(default=False, help_text="SUNAT's indMultipagina.")

    # metadataCampos describes the columns; lisCampos holds the offending records.
    field_metadata = models.JSONField(default=dict, blank=True)
    records = models.JSONField(default=list, blank=True)
    record_count = models.PositiveIntegerField(default=0)
    observation = models.JSONField(
        null=True, blank=True,
        help_text="SUNAT's observacion: the descargo/appeal state for this variable.",
    )

    objects = ComplianceVariableQuerySet.as_manager()

    class Meta:
        ordering = ["variable_type", "code", "created_at"]
        indexes = [models.Index(fields=["rating", "variable_type"])]

    def __str__(self) -> str:
        return f"{self.code} ({self.severity or 'sin gravedad'})"
