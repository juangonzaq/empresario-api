"""Items from SUNAFIL's casilla electrónica."""

from __future__ import annotations

from django.db import models

from core.models import BaseModel
from suppliers.validators import validate_ruc


class ItemKind(models.TextChoices):
    """The four listings the casilla exposes to an employer."""

    ORIENTATION = "orientation", "Sunafil te Orienta"
    REQUIREMENT = "requirement", "Acciones previas / requerimientos"
    INSPECTION_NOTICE = "inspection_notice", "Notificación de fiscalización"
    COLLECTION_NOTICE = "collection_notice", "Notificación de cobranza"


class SunafilItemQuerySet(models.QuerySet):
    def orientations(self) -> "SunafilItemQuerySet":
        return self.filter(kind=ItemKind.ORIENTATION)

    def unread(self) -> "SunafilItemQuerySet":
        return self.filter(is_read=False)

    def actionable(self) -> "SunafilItemQuerySet":
        """Items that carry an obligation: requirements and notifications."""
        return self.exclude(kind=ItemKind.ORIENTATION)

    def pending_detail(self) -> "SunafilItemQuerySet":
        return self.filter(kind=ItemKind.ORIENTATION, detail_fetched_at__isnull=True)


class SunafilItem(BaseModel):
    """One row of a casilla listing, plus the detail when it is safe to fetch."""

    taxpayer_id = models.CharField(
        "RUC", max_length=11, db_index=True, validators=[validate_ruc]
    )
    kind = models.CharField(max_length=20, choices=ItemKind, db_index=True)
    external_key = models.CharField(
        max_length=32,
        help_text="Hash of the columns that identify this row across runs.",
    )

    subject = models.TextField(blank=True, help_text="Asunto, or the requirement type.")
    category = models.CharField(max_length=120, blank=True)
    record_number = models.CharField(
        max_length=120, blank=True, db_index=True,
        help_text="Registro / orden de inspección / expediente sancionador.",
    )
    office = models.CharField(max_length=120, blank=True, help_text="Intendencia.")
    status = models.CharField(max_length=60, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)

    deposited_at = models.DateTimeField(null=True, blank=True, db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    notified_on = models.DateField(null=True, blank=True)
    due_date = models.DateField(
        null=True, blank=True, db_index=True,
        help_text="Fecha límite de presentación.",
    )
    deadline_days = models.PositiveIntegerField(null=True, blank=True)

    # The listing row exactly as SUNAFIL rendered it, keyed by column header.
    row = models.JSONField(default=dict, blank=True)

    # Detail, only ever populated for orientations — see the sync notes.
    detail_text = models.TextField(blank=True)
    detail_html = models.TextField(blank=True)
    detail_links = models.JSONField(default=list, blank=True)
    detail_images = models.JSONField(default=list, blank=True)
    detail_fetched_at = models.DateTimeField(null=True, blank=True)

    first_seen_on = models.DateField(db_index=True)
    last_seen_on = models.DateField(db_index=True)

    objects = SunafilItemQuerySet.as_manager()

    class Meta:
        ordering = ["-deposited_at", "-first_seen_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "kind", "external_key"],
                name="unique_sunafil_item",
            )
        ]
        indexes = [
            models.Index(fields=["taxpayer_id", "kind", "-deposited_at"]),
            models.Index(fields=["kind", "is_read"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_kind_display()}] {self.subject[:60]}"

    @property
    def has_detail(self) -> bool:
        return bool(self.detail_fetched_at)
