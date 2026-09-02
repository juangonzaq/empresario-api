from django.contrib import admin

from core.admin import filtro_empresa

from .models import (
    FinanceAiSummary, FinanceAlert, FinancePeriodClose, InvoiceExtract,
    InvoiceOverride, ManualEntry, RentaProjection,
)


@admin.register(InvoiceExtract)
class InvoiceExtractAdmin(admin.ModelAdmin):
    list_display = ("invoice", "status", "currency", "total_amount", "igv_amount")
    list_filter = ("status", "currency")
    search_fields = ("invoice__full_number", "reference_id")


@admin.register(FinanceAlert)
class FinanceAlertAdmin(admin.ModelAdmin):
    list_display = ("title", "alert_type", "severity", "period", "status")
    list_filter = (filtro_empresa("account_ruc"), "alert_type", "severity", "status")
    search_fields = ("title", "explanation")


@admin.register(FinanceAiSummary)
class FinanceAiSummaryAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "period", "model_name", "created_at")
    list_filter = (filtro_empresa("account_ruc"),)


@admin.register(ManualEntry)
class ManualEntryAdmin(admin.ModelAdmin):
    list_display = (
        "description", "direction", "period", "currency", "amount", "account_ruc",
    )
    list_filter = (filtro_empresa("account_ruc"), "direction", "currency")
    search_fields = ("description", "counterparty", "account_ruc")


@admin.register(InvoiceOverride)
class InvoiceOverrideAdmin(admin.ModelAdmin):
    list_display = ("invoice", "total_amount", "counterparty", "updated_at")
    list_filter = (filtro_empresa("account_ruc"),)
    search_fields = ("invoice__full_number", "counterparty", "account_ruc")


@admin.register(RentaProjection)
class RentaProjectionAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "year", "monthly_sales", "monthly_expenses",
                    "monthly_payroll", "updated_by", "updated_at")
    list_filter = (filtro_empresa("account_ruc"),)
    search_fields = ("account_ruc",)


@admin.register(FinancePeriodClose)
class FinancePeriodCloseAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "period", "closed_by", "created_at")
    list_filter = (filtro_empresa("account_ruc"),)
    search_fields = ("account_ruc", "period")
