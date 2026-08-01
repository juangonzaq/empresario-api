"""REMYPE registry standing, recorded per RUC over time."""

from __future__ import annotations

from django.db import models

from core.models import BaseModel
from suppliers.validators import validate_ruc


class RemypeCheckQuerySet(models.QuerySet):
    def registered(self) -> "RemypeCheckQuerySet":
        return self.filter(is_registered=True)

    def active(self) -> "RemypeCheckQuerySet":
        """Registered and not struck off."""
        return self.filter(is_registered=True, deregistered_on__isnull=True)

    def latest_per_ruc(self) -> "RemypeCheckQuerySet":
        """The most recent check for each RUC."""
        return self.order_by("ruc", "-checked_on").distinct("ruc")


class RemypeCheck(BaseModel):
    """One day's REMYPE lookup for a RUC.

    REMYPE accreditation can be revoked (``deregistered_on``), so the history matters:
    ``changed`` marks the day a company entered or left the registry.
    """

    ruc = models.CharField("RUC", max_length=11, db_index=True, validators=[validate_ruc])
    checked_on = models.DateField(db_index=True)

    is_registered = models.BooleanField(default=False, db_index=True)
    business_name = models.CharField(max_length=255, blank=True)
    condition = models.CharField(
        max_length=120, blank=True, help_text="REMYPE's CONDICION, e.g. 'ACREDITADO COMO MICRO EMPRESA'."
    )
    situation = models.CharField(max_length=120, blank=True, help_text="SITUACIONEMPRESA.")
    mype_category = models.CharField(max_length=120, blank=True, help_text="FLG_MYPE.")
    file_number = models.CharField(max_length=60, blank=True, help_text="NUMEROFICHASOLICITUD.")
    registry_code = models.BigIntegerField(null=True, blank=True, help_text="N_CODREG.")

    requested_on = models.DateField(null=True, blank=True)
    accredited_on = models.DateField(null=True, blank=True)
    deregistered_on = models.DateField(null=True, blank=True)

    changed = models.BooleanField(
        default=False, db_index=True,
        help_text="The registry standing differs from the previous check.",
    )
    previous_condition = models.CharField(max_length=120, blank=True)

    succeeded = models.BooleanField(default=True)
    message = models.TextField(blank=True, help_text="REMYPE's message, or the error.")
    payload = models.JSONField(default=dict, blank=True)

    objects = RemypeCheckQuerySet.as_manager()

    class Meta:
        ordering = ["-checked_on", "ruc"]
        constraints = [
            models.UniqueConstraint(
                fields=["ruc", "checked_on"], name="unique_remype_check_per_day"
            )
        ]
        indexes = [models.Index(fields=["ruc", "-checked_on"])]
        verbose_name = "REMYPE check"
        verbose_name_plural = "REMYPE checks"

    def __str__(self) -> str:
        if not self.succeeded:
            return f"{self.ruc} {self.checked_on} failed"
        state = self.condition or ("registered" if self.is_registered else "not registered")
        return f"{self.ruc} {self.checked_on} {state}"

    @property
    def is_active(self) -> bool:
        return self.is_registered and self.deregistered_on is None
