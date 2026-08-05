"""Django admin is the whole UI of the VIGÍA prototype."""

from __future__ import annotations

from django.contrib import admin

from .models import (
    Alert,
    BoxSnapshot,
    Inconsistency,
    Period,
    PurchaseDoc,
    RawArtifact,
    SalesDoc,
    SscoEntry,
    Supplier,
)


@admin.register(Period)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ("book", "tax_period", "status", "synced_at")
    list_filter = ("book", "status")
    search_fields = ("tax_period",)


@admin.register(SalesDoc)
class SalesDocAdmin(admin.ModelAdmin):
    list_display = (
        "issue_date", "doc_type", "series", "number",
        "customer_ruc", "customer_name", "base_amount", "igv", "total",
    )
    list_filter = ("tax_period", "doc_type")
    search_fields = ("customer_ruc", "customer_name", "series", "number", "car_sunat")


@admin.register(PurchaseDoc)
class PurchaseDocAdmin(admin.ModelAdmin):
    list_display = (
        "issue_date", "doc_type", "series", "number",
        "supplier_ruc", "supplier_name", "base_amount", "igv", "total", "recognized",
    )
    list_filter = ("tax_period", "doc_type", "recognized")
    search_fields = ("supplier_ruc", "supplier_name", "series", "number", "car_sunat")
    actions = ("mark_recognized", "mark_not_recognized")

    @admin.action(description="Mark as recognized")
    def mark_recognized(self, request, queryset):
        queryset.update(recognized=True)

    @admin.action(description="Mark as NOT recognized")
    def mark_not_recognized(self, request, queryset):
        queryset.update(recognized=False)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "ruc", "business_name", "registry_status", "registry_condition",
        "in_ssco", "total_purchased", "igv_at_risk",
    )
    list_filter = ("in_ssco", "registry_status", "registry_condition")
    search_fields = ("ruc", "business_name")


@admin.register(SscoEntry)
class SscoEntryAdmin(admin.ModelAdmin):
    list_display = ("ruc", "business_name", "captured_at")
    search_fields = ("ruc", "business_name")


@admin.register(BoxSnapshot)
class BoxSnapshotAdmin(admin.ModelAdmin):
    list_display = ("book", "tax_period", "box", "amount", "captured_at")
    list_filter = ("book", "tax_period", "box")


@admin.register(Inconsistency)
class InconsistencyAdmin(admin.ModelAdmin):
    list_display = ("book", "tax_period", "kind", "resolved")
    list_filter = ("book", "tax_period", "resolved")
    search_fields = ("kind",)


@admin.register(RawArtifact)
class RawArtifactAdmin(admin.ModelAdmin):
    list_display = ("endpoint", "local_path", "created_at")
    list_filter = ("endpoint",)
    search_fields = ("local_path",)


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        "rule", "severity", "title", "amount_at_risk", "due_date",
        "status", "created_at",
    )
    list_filter = ("severity", "rule", "status")
    search_fields = ("title",)
    actions = ("mark_resolved",)

    @admin.action(description="Mark as resolved")
    def mark_resolved(self, request, queryset):
        queryset.update(status="RESOLVED")
