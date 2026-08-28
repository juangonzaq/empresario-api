"""Full SUNAT RUC profile, captured as a snapshot per RUC per month."""

from __future__ import annotations

from django.db import models

from core.models import BaseModel
from suppliers.validators import validate_ruc


class RucSnapshotQuerySet(models.QuerySet):
    def for_ruc(self, ruc: str) -> "RucSnapshotQuerySet":
        return self.filter(ruc=ruc)

    def with_risk(self) -> "RucSnapshotQuerySet":
        return self.filter(has_risk_signals=True)

    def latest_per_ruc(self) -> "RucSnapshotQuerySet":
        return self.order_by("ruc", "-captured_on").distinct("ruc")


class RucSnapshot(BaseModel):
    """Everything SUNAT publishes about a RUC at one point in time.

    The main table's fields are columns; each button's response is stored as a
    :class:`RucSection`. The risk flags are lifted out of those sections so they can
    be filtered without digging into JSON.
    """

    ruc = models.CharField("RUC", max_length=11, db_index=True, validators=[validate_ruc])
    captured_on = models.DateField(db_index=True)

    # Main consultation table
    business_name = models.CharField(max_length=255, blank=True)
    trade_name = models.CharField(max_length=255, blank=True)
    taxpayer_type = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=60, blank=True, db_index=True)
    condition = models.CharField(max_length=60, blank=True, db_index=True)
    fiscal_address = models.TextField(blank=True)
    economic_activities = models.TextField(blank=True)
    electronic_invoicing = models.TextField(blank=True)
    registries = models.TextField(blank=True)
    registered_on = models.DateField(null=True, blank=True)
    started_activities_on = models.DateField(null=True, blank=True)

    # Signals lifted from the button sections
    has_coactive_debt = models.BooleanField(default=False, db_index=True)
    has_tax_omissions = models.BooleanField(default=False, db_index=True)
    has_probatory_acts = models.BooleanField(default=False, db_index=True)
    reactiva_peru_debt = models.BooleanField(
        default=False, help_text="Coactive debt over 1 UIT under Reactiva Perú."
    )
    covid_guarantee_debt = models.BooleanField(
        default=False, help_text="Coactive debt over 1 UIT under the COVID-19 programme."
    )
    has_risk_signals = models.BooleanField(
        default=False, db_index=True,
        help_text="Any of the risk sections returned data.",
    )

    worker_count = models.PositiveIntegerField(
        null=True, blank=True, help_text="Workers declared in the most recent period."
    )
    latest_worker_period = models.CharField(max_length=7, blank=True)
    # Establecimientos anexos declarados (sin contar el domicilio fiscal).
    # None = la sección no se capturó; 0 = SUNAT dice que no tiene ninguno.
    branch_count = models.PositiveIntegerField(null=True, blank=True)

    changed = models.BooleanField(
        default=False, db_index=True,
        help_text="Status, condition or risk signals differ from the previous snapshot.",
    )
    change_summary = models.TextField(blank=True)

    succeeded = models.BooleanField(default=True)
    error = models.TextField(blank=True)

    objects = RucSnapshotQuerySet.as_manager()

    class Meta:
        ordering = ["-captured_on", "ruc"]
        constraints = [
            models.UniqueConstraint(
                fields=["ruc", "captured_on"], name="unique_ruc_snapshot_per_day"
            )
        ]
        indexes = [models.Index(fields=["ruc", "-captured_on"])]

    def __str__(self) -> str:
        return f"{self.ruc} {self.captured_on} {self.status or 'failed'}"


class RucSection(BaseModel):
    """One button's response, kept verbatim as parsed tables.

    Stored generically on purpose: SUNAT reshuffles these pages, and a JSON payload
    survives that where a bespoke column layout would not.
    """

    snapshot = models.ForeignKey(
        RucSnapshot, related_name="sections", on_delete=models.CASCADE
    )
    key = models.CharField(max_length=40, db_index=True)
    label = models.CharField(max_length=120, blank=True)
    title = models.TextField(blank=True)
    has_data = models.BooleanField(default=False)
    answer = models.BooleanField(
        null=True, blank=True, help_text="Yes/no sections only."
    )
    tables = models.JSONField(
        default=list, blank=True, help_text="[{headers: [...], rows: [[...]]}]"
    )
    text = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "key"], name="unique_section_per_snapshot"
            )
        ]

    def __str__(self) -> str:
        return f"{self.snapshot.ruc} {self.key}"


class LegalRepresentative(BaseModel):
    """A row of the *Representantes legales* section, kept queryable."""

    snapshot = models.ForeignKey(
        RucSnapshot, related_name="legal_representatives", on_delete=models.CASCADE
    )
    document_type = models.CharField(max_length=40, blank=True)
    document_number = models.CharField(max_length=20, blank=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=120, blank=True)
    since = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.role})"


class WorkerHeadcount(BaseModel):
    """A row of the *Cantidad de trabajadores* section: one month of PLAME figures."""

    snapshot = models.ForeignKey(
        RucSnapshot, related_name="headcounts", on_delete=models.CASCADE
    )
    period = models.CharField(max_length=7, db_index=True, help_text="YYYY-MM")
    workers = models.PositiveIntegerField(null=True, blank=True)
    pensioners = models.PositiveIntegerField(null=True, blank=True)
    service_providers = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-period"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "period"], name="unique_headcount_per_period"
            )
        ]

    def __str__(self) -> str:
        return f"{self.period}: {self.workers} workers"


class RucTaxAffectation(BaseModel):
    """Una fila de «Registro de Tributos Afectos» de la Ficha RUC en SOL.
    De aquí sale el régimen de renta de la empresa."""

    ruc = models.CharField("RUC", max_length=11, db_index=True)
    tributo = models.CharField(max_length=120)
    fecha_alta = models.DateField(null=True, blank=True)
    afecto_desde = models.DateField(null=True, blank=True)
    captured_at = models.DateTimeField()

    class Meta:
        ordering = ["ruc", "tributo"]
        verbose_name = "tributo afecto"
        verbose_name_plural = "tributos afectos"

    def __str__(self) -> str:
        return f"{self.ruc} · {self.tributo}"
