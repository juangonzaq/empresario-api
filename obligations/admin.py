from django.contrib import admin

from .models import (
    ComplianceAction, ComplianceDomain, ComplianceRule, ComplianceSnapshot,
    CompanyObligation, ObligationAssessment, ObligationEvidence,
)


@admin.register(ComplianceDomain)
class ComplianceDomainAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")


@admin.register(ComplianceRule)
class ComplianceRuleAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "domain", "obligation_type", "frequency",
                    "default_severity", "weight", "evaluator_key", "is_active", "sort_order")
    list_filter = ("domain", "obligation_type", "frequency", "default_severity", "is_active")
    list_editable = ("weight", "is_active", "sort_order")
    search_fields = ("code", "title", "summary", "legal_reference")


class EvidenceInline(admin.TabularInline):
    model = ObligationEvidence
    extra = 0


class ActionInline(admin.TabularInline):
    model = ComplianceAction
    extra = 0


@admin.register(CompanyObligation)
class CompanyObligationAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "rule", "applicability_status", "compliance_status",
                    "workflow_status", "verification_status", "severity", "due_date",
                    "last_evaluated_at")
    list_filter = ("applicability_status", "compliance_status", "workflow_status",
                   "verification_status", "severity", "rule__domain")
    search_fields = ("account_ruc", "rule__code", "rule__title")
    list_select_related = ("rule", "rule__domain")
    inlines = [EvidenceInline, ActionInline]


@admin.register(ObligationAssessment)
class ObligationAssessmentAdmin(admin.ModelAdmin):
    list_display = ("company_obligation", "compliance_status", "verification_status",
                    "evaluation_source", "created_at")
    list_filter = ("compliance_status", "verification_status", "evaluation_source")
    readonly_fields = tuple(f.name for f in ObligationAssessment._meta.fields)


@admin.register(ComplianceSnapshot)
class ComplianceSnapshotAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "snapshot_date", "overall_score", "applicable_count",
                    "compliant_count", "non_compliant_count", "overdue_count")
    list_filter = ("snapshot_date",)
    search_fields = ("account_ruc",)
