"""Suppliers and the daily record of their SUNAT standing."""

from __future__ import annotations

from django.db import models

from core.models import BaseModel

from .validators import validate_ruc


class SupplierQuerySet(models.QuerySet):
    def tracked(self) -> "SupplierQuerySet":
        return self.filter(is_tracked=True)

    def with_issues(self) -> "SupplierQuerySet":
        return self.filter(has_issue=True)

    def never_checked(self) -> "SupplierQuerySet":
        return self.filter(last_checked_at__isnull=True)


class Supplier(BaseModel):
    """A supplier whose SUNAT standing is monitored.

    The ``status``/``condition``/``has_issue`` fields mirror the most recent check
    so lists and filters stay cheap; the full history lives in :class:`SupplierCheck`.
    """

    ruc = models.CharField(
        "RUC", max_length=11, unique=True, validators=[validate_ruc]
    )
    alias = models.CharField(
        max_length=120, blank=True,
        help_text="Internal name for this supplier. Falls back to the SUNAT name.",
    )
    business_name = models.CharField(
        max_length=255, blank=True, help_text="Razón social, as reported by SUNAT."
    )
    trade_name = models.CharField(max_length=255, blank=True)
    taxpayer_type = models.CharField(max_length=120, blank=True)
    fiscal_address = models.TextField(blank=True)
    economic_activities = models.TextField(blank=True)
    registered_on = models.DateField(null=True, blank=True)
    started_activities_on = models.DateField(null=True, blank=True)

    is_tracked = models.BooleanField(
        default=True, help_text="Untracked suppliers are skipped by the daily check."
    )
    notes = models.TextField(blank=True)

    # Latest known standing, refreshed on every check.
    status = models.CharField(max_length=60, blank=True, db_index=True)
    condition = models.CharField(max_length=60, blank=True, db_index=True)
    has_issue = models.BooleanField(default=False, db_index=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_changed_at = models.DateTimeField(
        null=True, blank=True, help_text="When the status or condition last changed."
    )
    last_error = models.TextField(
        blank=True, help_text="Why the most recent check failed, if it did."
    )

    objects = SupplierQuerySet.as_manager()

    class Meta:
        ordering = ["alias", "business_name", "ruc"]
        indexes = [models.Index(fields=["is_tracked", "has_issue"])]

    def __str__(self) -> str:
        return f"{self.ruc} — {self.display_name}"

    @property
    def display_name(self) -> str:
        return self.alias or self.business_name or self.ruc


class SupplierCheck(BaseModel):
    """One day's snapshot of a supplier's SUNAT standing."""

    supplier = models.ForeignKey(
        Supplier, related_name="checks", on_delete=models.CASCADE
    )
    checked_on = models.DateField(db_index=True)

    status = models.CharField(max_length=60, blank=True)
    condition = models.CharField(max_length=60, blank=True)
    has_issue = models.BooleanField(default=False, db_index=True)

    # Set when this check differs from the previous one; this is what makes the
    # history worth keeping, since it is the moment a supplier's standing moved.
    changed = models.BooleanField(default=False, db_index=True)
    previous_status = models.CharField(max_length=60, blank=True)
    previous_condition = models.CharField(max_length=60, blank=True)

    succeeded = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-checked_on", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "checked_on"], name="unique_supplier_check_per_day"
            )
        ]
        indexes = [models.Index(fields=["supplier", "-checked_on"])]

    def __str__(self) -> str:
        outcome = f"{self.status}/{self.condition}" if self.succeeded else "failed"
        return f"{self.supplier.ruc} {self.checked_on} {outcome}"
