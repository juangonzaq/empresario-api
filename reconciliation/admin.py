from django.contrib import admin

from core.admin import filtro_empresa

from .models import (
    BankMovement, ConsistencyScore, DeclaredSummary, DocumentReconciliation,
    BankStatement, InvoiceSettlement, ReconciliationRun, SettlementLine,
)


@admin.register(ReconciliationRun)
class RunAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "period", "status", "created_at", "finished_at")
    list_filter = (filtro_empresa("account_ruc"), "status")
    readonly_fields = ("totals", "findings_count", "error")


@admin.register(DocumentReconciliation)
class DocAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "period", "direction", "doc_key", "status", "level", "cpe_total", "sire_total")
    list_filter = (filtro_empresa("account_ruc"), "direction", "status", "level")
    search_fields = ("doc_key", "counterparty_ruc", "counterparty_name")


@admin.register(DeclaredSummary)
class DeclaredAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "period", "sales_base", "purchases_base", "igv_payable", "source", "filed_at")
    list_filter = (filtro_empresa("account_ruc"), "source")


@admin.register(BankMovement)
class MovementAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "date", "kind", "amount", "category", "confidence", "classified_by", "description")
    list_filter = (filtro_empresa("account_ruc"), "kind", "category", "classified_by", "bank")
    search_fields = ("description", "operation_number")


class LineInline(admin.TabularInline):
    model = SettlementLine
    extra = 0


@admin.register(InvoiceSettlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "invoice", "status", "invoice_total", "paid_amount", "balance", "billing_period", "collection_period")
    list_filter = (filtro_empresa("account_ruc"), "status")
    inlines = [LineInline]


@admin.register(ConsistencyScore)
class ScoreAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "period", "score")
    list_filter = (filtro_empresa("account_ruc"),)


@admin.register(BankStatement)
class BankStatementAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "bank", "currency", "status", "movement_count", "period_from", "period_to", "created_at")
    list_filter = (filtro_empresa("account_ruc"), "status", "currency", "bank")
    search_fields = ("account_ruc", "bank", "original_name")
