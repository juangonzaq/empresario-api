"""Admin for the master tables (§11 phase 1: a working admin is the
deliverable that lets someone maintain rates without touching code)."""

from django.contrib import admin

from .models import (
    EmployeeSalaryHistory, IncomeTaxBracket, IncomeTaxProjection,
    IncomeTaxSettings, MinimumWage, PayrollConcept, PayrollEntry,
    PayrollPeriod, PayrollSettings, PensionFundRate, SocialHealthRate,
    TaxUnitValue, WorkRiskInsuranceRate,
)


@admin.register(TaxUnitValue)
class TaxUnitValueAdmin(admin.ModelAdmin):
    list_display = ("year", "amount")


@admin.register(MinimumWage)
class MinimumWageAdmin(admin.ModelAdmin):
    list_display = ("amount", "effective_from", "effective_to")


@admin.register(PensionFundRate)
class PensionFundRateAdmin(admin.ModelAdmin):
    list_display = (
        "pension_fund", "commission_type", "mandatory_contribution_rate",
        "flow_commission_rate", "insurance_premium_rate", "insurable_ceiling",
        "effective_from", "effective_to",
    )
    list_filter = ("pension_fund", "commission_type")


@admin.register(IncomeTaxBracket)
class IncomeTaxBracketAdmin(admin.ModelAdmin):
    list_display = ("year", "order", "width_in_tax_units", "rate")
    list_filter = ("year",)


@admin.register(IncomeTaxSettings)
class IncomeTaxSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "year", "standard_deduction_in_tax_units", "months_in_projection",
        "statutory_bonus_count",
    )


@admin.register(SocialHealthRate)
class SocialHealthRateAdmin(admin.ModelAdmin):
    list_display = (
        "standard_rate", "eps_rate", "applies_minimum_wage_floor",
        "effective_from", "effective_to",
    )


@admin.register(WorkRiskInsuranceRate)
class WorkRiskInsuranceRateAdmin(admin.ModelAdmin):
    list_display = (
        "activity_code", "health_rate", "pension_rate", "effective_from",
    )


@admin.register(PayrollSettings)
class PayrollSettingsAdmin(admin.ModelAdmin):
    list_display = ("taxpayer_id", "days_per_month", "hours_per_day")


@admin.register(PayrollConcept)
class PayrollConceptAdmin(admin.ModelAdmin):
    list_display = (
        "code", "name", "kind", "affects_pension_base",
        "affects_income_tax_base", "is_computed", "display_order",
        "taxpayer_id", "is_active",
    )
    list_filter = ("kind", "is_computed", "is_active")
    search_fields = ("code", "name")


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = ("taxpayer_id", "year", "month", "status", "closed_at")
    list_filter = ("status",)


@admin.register(PayrollEntry)
class PayrollEntryAdmin(admin.ModelAdmin):
    list_display = ("colaborador", "period", "gross_pay", "net_pay")
    search_fields = ("colaborador__full_name",)


@admin.register(EmployeeSalaryHistory)
class EmployeeSalaryHistoryAdmin(admin.ModelAdmin):
    list_display = ("colaborador", "amount", "effective_from", "reason")


@admin.register(IncomeTaxProjection)
class IncomeTaxProjectionAdmin(admin.ModelAdmin):
    list_display = ("colaborador", "year", "taxable_income", "annual_tax")
