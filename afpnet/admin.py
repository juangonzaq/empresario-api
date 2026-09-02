"""Admin de AFPnet: la app entera era invisible en el admin.

Todo lo escribe el scraper — aquí se consulta y se diagnostica, no se edita.
Los aportes de un afiliado cuelgan de su ficha (inline), que es como se
revisan: por persona, no como lista suelta.
"""

from django.contrib import admin

from core.admin import filtro_empresa

from .models import (
    AfpnetAffiliate, AfpnetCompany, AfpnetContribution, AfpnetDebt,
    AfpnetDeclaration, AfpnetDocument, AfpnetPeriodSummary, AfpnetSession,
)


class SoloConsulta(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False


@admin.register(AfpnetSession)
class AfpnetSessionAdmin(SoloConsulta):
    list_display = ("organization", "taxpayer_id", "username", "status",
                    "opened_at", "last_used_at")
    list_filter = (filtro_empresa("taxpayer_id"), "status")
    exclude = ("encrypted_cookies",)
    readonly_fields = ("opened_at", "last_used_at", "last_error")


@admin.register(AfpnetCompany)
class AfpnetCompanyAdmin(SoloConsulta):
    list_display = ("organization", "taxpayer_id", "legal_name", "representative",
                    "fetched_at")
    list_filter = (filtro_empresa("taxpayer_id"),)
    search_fields = ("taxpayer_id", "legal_name", "representative")


@admin.register(AfpnetDeclaration)
class AfpnetDeclarationAdmin(SoloConsulta):
    list_display = ("taxpayer_id", "period", "afp", "presentation_number",
                    "presented_at", "nominal_fondo", "is_paid", "state")
    list_filter = (filtro_empresa("taxpayer_id"), "afp", "is_paid", "state")
    search_fields = ("presentation_number", "ticket", "period")
    readonly_fields = ("raw",)


@admin.register(AfpnetDebt)
class AfpnetDebtAdmin(SoloConsulta):
    list_display = ("taxpayer_id", "period", "afp", "concept", "principal",
                    "interest", "total", "due_on", "state")
    list_filter = (filtro_empresa("taxpayer_id"), "afp", "state")
    search_fields = ("period", "concept")
    readonly_fields = ("raw",)


class AfpnetContributionInline(admin.TabularInline):
    model = AfpnetContribution
    extra = 0
    can_delete = False
    fields = ("period", "kind", "remuneration", "obliged_fund", "declared_fund",
              "paid_fund", "declared_unpaid")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(AfpnetAffiliate)
class AfpnetAffiliateAdmin(SoloConsulta):
    list_display = ("full_name", "taxpayer_id", "cuspp", "afp", "commission_type",
                    "affiliated_on", "is_active")
    list_filter = (filtro_empresa("taxpayer_id"), "afp", "is_active", "commission_type")
    search_fields = ("full_name", "cuspp", "document_number")
    inlines = (AfpnetContributionInline,)
    readonly_fields = ("raw",)


@admin.register(AfpnetDocument)
class AfpnetDocumentAdmin(SoloConsulta):
    list_display = ("taxpayer_id", "kind", "period", "afp", "number", "issued_on")
    list_filter = (filtro_empresa("taxpayer_id"), "kind", "afp")
    search_fields = ("number", "period")


@admin.register(AfpnetPeriodSummary)
class AfpnetPeriodSummaryAdmin(SoloConsulta):
    list_display = ("taxpayer_id", "period", "total_op", "op_cierta", "op_presunta",
                    "op_con_deuda", "op_sin_deuda")
    list_filter = (filtro_empresa("taxpayer_id"),)
    search_fields = ("period",)
