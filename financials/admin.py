from django.contrib import admin

from .models import (
    CategorizationRule, Counterparty, ExchangeRate, FinancialSettings,
    FinancialTransaction, ManualBalanceEntry, RatioDefinition, RatioThreshold,
    StatementLine, TaxRate, TransactionCategory,
)


@admin.register(FinancialTransaction)
class FinancialTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "external_id", "source", "direction", "accounting_date",
        "counterparty_name", "net_amount_pen", "categorization_status",
        "taxpayer_id",
    )
    list_filter = ("source", "categorization_status", "direction")
    search_fields = ("external_id", "counterparty_name", "counterparty_tax_id")
    date_hierarchy = "accounting_date"


@admin.register(TransactionCategory)
class TransactionCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "statement", "statement_line", "sign", "taxpayer_id")
    list_filter = ("statement",)
    search_fields = ("code", "name")


@admin.register(StatementLine)
class StatementLineAdmin(admin.ModelAdmin):
    list_display = ("statement", "code", "name", "line_type", "display_order")
    list_filter = ("statement", "line_type")


@admin.register(RatioDefinition)
class RatioDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "group", "unit", "is_active")
    list_filter = ("group",)


@admin.register(RatioThreshold)
class RatioThresholdAdmin(admin.ModelAdmin):
    list_display = ("ratio", "label", "min_value", "max_value", "severity")


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ("tax_code", "rate", "effective_from", "effective_to")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("currency", "rate_date", "buy_rate", "sell_rate")


@admin.register(Counterparty)
class CounterpartyAdmin(admin.ModelAdmin):
    list_display = ("tax_id", "legal_name", "kind", "default_category", "taxpayer_id")
    search_fields = ("tax_id", "legal_name")


@admin.register(CategorizationRule)
class CategorizationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "priority", "category", "confidence", "match_count", "is_active")


@admin.register(FinancialSettings)
class FinancialSettingsAdmin(admin.ModelAdmin):
    list_display = ("taxpayer_id", "functional_currency", "display_scale")


@admin.register(ManualBalanceEntry)
class ManualBalanceEntryAdmin(admin.ModelAdmin):
    list_display = ("category", "year", "month", "amount", "taxpayer_id")
