"""VIGÍA prototype models — single implicit tenant (PATTERN GROUP S.A.C.).

Django admin is the dashboard, so every model is registered in admin.py with
filters and search. Raw SUNAT files live on disk (RawArtifact tracks them).
"""

from __future__ import annotations

from django.db import models


class Book(models.TextChoices):
    RVIE = "RVIE", "RVIE (sales)"
    RCE = "RCE", "RCE (purchases)"


class Period(models.Model):
    """One tax period (yyyymm) per electronic book, as SUNAT reports it."""

    book = models.CharField(max_length=4, choices=Book)
    tax_period = models.CharField(max_length=6)              # yyyymm
    status = models.CharField(
        max_length=60, default="?",
        help_text="SUNAT's desEstado for the period, stored verbatim.",
    )
    status_code = models.CharField(max_length=4, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("book", "tax_period")
        ordering = ["-tax_period", "book"]

    def __str__(self) -> str:
        return f"{self.book} {self.tax_period}: {self.status}"


class SalesDoc(models.Model):
    """One row of the RVIE proposal (a sales invoice/note SUNAT knows about)."""

    tax_period = models.CharField(max_length=6, db_index=True)
    doc_type = models.CharField(max_length=2)                # tipo CDP (tabla 03)
    series = models.CharField(max_length=8)
    number = models.CharField(max_length=12)
    issue_date = models.DateField(null=True)
    customer_ruc = models.CharField(max_length=15, blank=True)
    customer_name = models.CharField(max_length=200, blank=True)
    base_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    igv = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    car_sunat = models.CharField(max_length=35, blank=True)
    raw_extra = models.JSONField(default=dict)               # unmapped TXT columns

    class Meta:
        unique_together = ("doc_type", "series", "number")
        ordering = ["-issue_date"]

    def __str__(self) -> str:
        return f"{self.doc_type} {self.series}-{self.number}"


class PurchaseDoc(models.Model):
    """One row of the RCE proposal (a purchase document SUNAT knows about)."""

    tax_period = models.CharField(max_length=6, db_index=True)
    doc_type = models.CharField(max_length=2)
    series = models.CharField(max_length=8)
    number = models.CharField(max_length=12)
    issue_date = models.DateField(null=True)
    supplier_ruc = models.CharField(max_length=15, blank=True)
    supplier_name = models.CharField(max_length=200, blank=True)
    base_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    igv = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    car_sunat = models.CharField(max_length=35, blank=True)
    raw_extra = models.JSONField(default=dict)
    recognized = models.BooleanField(
        null=True, help_text="None = not reviewed yet (rule R6).",
    )
    first_seen = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("doc_type", "series", "number")
        ordering = ["-issue_date"]

    def __str__(self) -> str:
        return f"{self.doc_type} {self.series}-{self.number} ({self.supplier_ruc})"


class Supplier(models.Model):
    """Aggregate per supplier RUC, rebuilt from PurchaseDoc on each RCE sync."""

    ruc = models.CharField(max_length=11, unique=True)
    business_name = models.CharField(max_length=200, blank=True)
    registry_status = models.CharField(max_length=30, blank=True)     # ACTIVO / BAJA...
    registry_condition = models.CharField(max_length=30, blank=True)  # HABIDO / NO HABIDO
    in_ssco = models.BooleanField(default=False)
    total_purchased = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igv_at_risk = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["-total_purchased"]

    def __str__(self) -> str:
        return f"{self.ruc} {self.business_name}"


class SscoEntry(models.Model):
    """A RUC on SUNAT's 'sujetos sin capacidad operativa' public list."""

    ruc = models.CharField(max_length=11, unique=True)
    business_name = models.CharField(max_length=200, blank=True)
    detail = models.JSONField(default=dict)
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "SSCO entry"
        verbose_name_plural = "SSCO entries"

    def __str__(self) -> str:
        return f"{self.ruc} {self.business_name}"


class BoxSnapshot(models.Model):
    """One IGV-return box (casilla) amount from the SIRE casillas report."""

    book = models.CharField(max_length=4, choices=Book)
    tax_period = models.CharField(max_length=6, db_index=True)
    box = models.CharField(max_length=4)                     # 100, 101, 102...
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    captured_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("book", "tax_period", "box")
        ordering = ["-tax_period", "box"]

    def __str__(self) -> str:
        return f"{self.book} {self.tax_period} box {self.box}: {self.amount}"


class Inconsistency(models.Model):
    """One inconsistency SUNAT reports for a period (per-document or per-box)."""

    book = models.CharField(max_length=4, choices=Book)
    tax_period = models.CharField(max_length=6, db_index=True)
    kind = models.CharField(max_length=60)
    detail = models.JSONField(default=dict)
    resolved = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "inconsistencies"
        ordering = ["-tax_period"]

    def __str__(self) -> str:
        return f"{self.book} {self.tax_period}: {self.kind}"


class RawArtifact(models.Model):
    """Pointer to a raw SUNAT download saved under MEDIA_SUNAT_DIR."""

    endpoint = models.CharField(max_length=80)
    params = models.JSONField(default=dict)
    local_path = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.endpoint} → {self.local_path}"


class Alert(models.Model):
    """Output of the rules engine (rules.py). One OPEN alert per rule+object."""

    class Severity(models.TextChoices):
        RED = "ROJO", "Rojo"
        AMBER = "AMBAR", "Ámbar"
        INFO = "INFO", "Info"

    rule = models.CharField(max_length=6)                    # R1..R12
    severity = models.CharField(max_length=6, choices=Severity)
    title = models.CharField(max_length=200)
    detail = models.JSONField(default=dict)
    amount_at_risk = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    due_date = models.DateField(null=True)
    status = models.CharField(max_length=10, default="OPEN")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "severity", "-created_at"]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.rule}: {self.title}"
