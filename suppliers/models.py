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

    # Cada empresa lleva su propia cartera: el mismo proveedor puede estar en
    # la lista de varias, con alias y notas distintas. Por eso el RUC deja de
    # ser único global y pasa a serlo por (empresa, proveedor).
    account_ruc = models.CharField(
        "RUC de la empresa", max_length=11, db_index=True, default="",
        help_text="Empresa dueña de esta ficha de proveedor.",
    )
    ruc = models.CharField(
        "RUC", max_length=11, db_index=True, validators=[validate_ruc]
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
        constraints = [
            models.UniqueConstraint(
                fields=["account_ruc", "ruc"],
                name="unique_supplier_per_account",
            )
        ]
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


class SscoQuerySet(models.QuerySet):
    def vigentes(self) -> "SscoQuerySet":
        return self.filter(vigente=True)


class SujetoSinCapacidadOperativa(BaseModel):
    """Un RUC del padrón de Sujetos Sin Capacidad Operativa (SSCO) de SUNAT.

    Es la lista más dura que publica SUNAT: contribuyentes a los que, por
    resolución firme, se les ha atribuido que no tienen recursos para haber
    hecho las operaciones que facturaron (D. Leg. 1532). Las facturas de un
    SSCO no dan crédito fiscal ni gasto, y punto: no hay «prueba en contrario».

    El padrón es **global**, no de una empresa: se descarga una vez al mes del
    Excel público y cualquier cartera lo cruza. Un RUC que deja de aparecer no
    se borra —importa saber que estuvo— sino que pasa a ``vigente=False``.
    """

    ruc = models.CharField("RUC", max_length=11, unique=True)
    razon_social = models.CharField(max_length=255, blank=True)
    domicilio_fiscal = models.TextField(blank=True)
    resolucion = models.CharField(max_length=160, blank=True)
    fecha_resolucion = models.DateField(null=True, blank=True)
    fecha_firme = models.DateField(
        null=True, blank=True, help_text="Cuándo la resolución quedó firme.",
    )
    # Puede traer varios documentos separados por comas, de ahí texto libre.
    representante_documento = models.TextField(blank=True)
    representante_nombre = models.CharField(max_length=255, blank=True)
    fecha_publicacion = models.DateField(null=True, blank=True)

    vigente = models.BooleanField(default=True, db_index=True)
    visto_el = models.DateField(help_text="Última descarga del padrón en la que apareció.")

    objects = SscoQuerySet.as_manager()

    class Meta:
        verbose_name = "sujeto sin capacidad operativa"
        verbose_name_plural = "sujetos sin capacidad operativa"
        ordering = ["-fecha_publicacion", "ruc"]

    def __str__(self) -> str:
        return f"{self.ruc} — {self.razon_social}"
