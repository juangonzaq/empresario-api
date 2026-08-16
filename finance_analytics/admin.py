from django.contrib import admin

from .models import (
    FinanceAiSummary, FinanceAlert, InvoiceExtract, InvoiceOverride,
    ManualEntry,
)


@admin.register(InvoiceExtract)
class InvoiceExtractAdmin(admin.ModelAdmin):
    list_display = ("invoice", "status", "currency", "total_amount", "igv_amount")
    list_filter = ("status", "currency")
    search_fields = ("invoice__full_number", "reference_id")


@admin.register(FinanceAlert)
class FinanceAlertAdmin(admin.ModelAdmin):
    list_display = ("title", "alert_type", "severity", "period", "status")
    list_filter = ("alert_type", "severity", "status")
    search_fields = ("title", "explanation")


@admin.register(FinanceAiSummary)
class FinanceAiSummaryAdmin(admin.ModelAdmin):
    list_display = ("period", "model_name", "created_at")


@admin.register(ManualEntry)
class ManualEntryAdmin(admin.ModelAdmin):
    list_display = (
        "description", "direction", "period", "currency", "amount", "account_ruc",
    )
    list_filter = ("direction", "currency")
    search_fields = ("description", "counterparty", "account_ruc")


@admin.register(InvoiceOverride)
class InvoiceOverrideAdmin(admin.ModelAdmin):
    list_display = ("invoice", "total_amount", "counterparty", "updated_at")
    search_fields = ("invoice__full_number", "counterparty", "account_ruc")
