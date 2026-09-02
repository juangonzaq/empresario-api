from django.apps import apps
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from billing.models import Referral, ReferralReward, Subscription
from .models import (
    BusinessProfile, Invitation, Membership, OneTimeToken, Organization,
    SunatAuthorization, SunatCredential, User,
)


class SoloLecturaInline(admin.TabularInline):
    """Historial que escribe el sistema: se consulta, no se edita a mano."""

    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class MembershipUserInline(admin.TabularInline):
    model = Membership
    fk_name = "user"
    verbose_name = "empresa"
    verbose_name_plural = "Empresas (membresías)"
    extra = 0
    fields = ("organization", "role", "is_active", "invited_by", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("organization", "invited_by")
    show_change_link = True


class OneTimeTokenInline(SoloLecturaInline):
    model = OneTimeToken
    verbose_name_plural = "Tokens de verificación y recuperación"
    fields = ("purpose", "token", "created_at", "expires_at", "used_at", "vigente")
    readonly_fields = fields

    @admin.display(boolean=True, description="vigente")
    def vigente(self, token: OneTimeToken) -> bool:
        return token.is_usable


class SunatAuthorizationUserInline(SoloLecturaInline):
    model = SunatAuthorization
    fk_name = "user"
    verbose_name_plural = "Autorizaciones SUNAT aceptadas"
    fields = ("organization", "sol_username", "version", "accepted_at",
              "ip_address", "revoked_at")
    readonly_fields = fields


class ReferralInline(SoloLecturaInline):
    model = Referral
    fk_name = "referrer"
    verbose_name_plural = "Referidos que trajo"
    fields = ("referred", "created_at", "converted_at")
    readonly_fields = fields


class ReferralRewardInline(SoloLecturaInline):
    model = ReferralReward
    verbose_name_plural = "Premios por referidos"
    fields = ("days", "conversions_at_grant", "applied_to", "applied_at", "created_at")
    readonly_fields = fields


class VerificadoFilter(admin.SimpleListFilter):
    """`email_verified_at` es una fecha; para filtrar lo que importa es si existe."""

    title = "correo verificado"
    parameter_name = "verificado"

    def lookups(self, request, model_admin):
        return (("si", "Sí"), ("no", "No"))

    def queryset(self, request, queryset):
        if self.value() == "si":
            return queryset.filter(email_verified_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(email_verified_at__isnull=True)
        return queryset


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "nombre", "phone", "companies_summary",
                    "extra_company_seats", "verificado", "is_active", "is_staff",
                    "created_at", "last_login")
    list_filter = (VerificadoFilter, "is_active", "is_staff", "is_superuser",
                   "memberships__role", "created_at")
    list_editable = ("extra_company_seats",)
    date_hierarchy = "created_at"
    # Por empresa también: «¿quiénes entran a la empresa con RUC …?» se
    # responde buscando el RUC o la razón social aquí mismo.
    search_fields = ("email", "first_name", "last_name", "phone", "referral_code",
                     "memberships__organization__ruc",
                     "memberships__organization__name")
    readonly_fields = ("last_login", "created_at", "updated_at",
                       "companies_summary", "referral_code")
    autocomplete_fields = ("referred_by",)
    inlines = [MembershipUserInline, SunatAuthorizationUserInline,
               ReferralInline, ReferralRewardInline, OneTimeTokenInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Datos", {"fields": ("first_name", "last_name", "phone")}),
        # Asientos de empresa: los que incluye el plan del titular más estos
        # extra que le otorgas. `companies_summary` muestra el uso actual.
        ("Empresas y asientos", {"fields": ("extra_company_seats", "companies_summary")}),
        ("Referidos", {"fields": ("referral_code", "referred_by")}),
        ("Estado", {"fields": ("is_active", "email_verified_at")}),
        ("Permisos", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Fechas", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2"),
        }),
    )

    def get_search_results(self, request, queryset, search_term):
        # Buscar por empresa recorre memberships (un join uno-a-muchos):
        # sin distinct, un usuario con varias empresas saldría repetido.
        queryset, _ = super().get_search_results(request, queryset, search_term)
        return queryset.distinct(), False

    @admin.display(description="Nombre", ordering="first_name")
    def nombre(self, user: User) -> str:
        return user.full_name or "—"

    @admin.display(boolean=True, description="Verificado", ordering="email_verified_at")
    def verificado(self, user: User) -> bool:
        return user.email_verified

    @admin.display(description="Empresas (uso / tope)")
    def companies_summary(self, user: User) -> str:
        from billing.services import seat_summary

        s = seat_summary(user)
        return f"{s['used']} / {s['limit']}  (plan {s['included']} + extra {s['extra']})"


class MembershipInline(admin.TabularInline):
    model = Membership
    fk_name = "organization"
    extra = 0
    fields = ("user", "role", "is_active", "invited_by")
    autocomplete_fields = ("user", "invited_by")


class InvitationInline(admin.TabularInline):
    model = Invitation
    extra = 0
    fields = ("email", "role", "status", "invited_by", "accepted_user", "created_at")
    readonly_fields = ("status", "accepted_user", "created_at")
    autocomplete_fields = ("invited_by",)


class SunatCredentialInline(SoloLecturaInline):
    model = SunatCredential
    verbose_name_plural = "Credencial SUNAT (SOL)"
    fields = ("sol_username", "status", "uses_primary_user", "last_verified_at", "last_error")
    readonly_fields = fields


class BusinessProfileInline(admin.StackedInline):
    model = BusinessProfile
    verbose_name_plural = "Perfil del negocio (onboarding)"
    extra = 0
    fields = ("offerings", "sectors", "goals", "business_age", "people_count",
              "sells_to_consumers", "has_premises", "sells_online", "has_payroll",
              "completed_at")
    readonly_fields = ("completed_at",)


class SubscriptionInline(SoloLecturaInline):
    model = Subscription
    verbose_name_plural = "Suscripción"
    fields = ("plan", "trial_end", "current_period_end", "auto_renew",
              "gateway", "canceled_at")
    readonly_fields = fields


# El mapa de la empresa: cada módulo tenant con su campo hacia el RUC. Los
# enlaces usan el parámetro común ?empresa= (core.admin.filtro_empresa), así
# que todo modelo listado aquí DEBE llevar ese filtro en su ModelAdmin.
PANORAMA: tuple[tuple[str, tuple[tuple[str, str, str, str], ...]], ...] = (
    ("Sincronización y buzón", (
        ("sync", "SyncJob", "organization__ruc", "Sincronizaciones"),
        ("sunat_mailbox", "Message", "taxpayer_id", "Mensajes del buzón"),
        ("sunat_intel", "Case", "taxpayer_id", "Casos del buzón (IA)"),
        ("sunat_intel", "VigiaMessage", "taxpayer_id", "Avisos Vigía"),
    )),
    ("Comprobantes e ingresos", (
        ("sunat_cpe", "ElectronicInvoice", "account_ruc", "Comprobantes electrónicos"),
        ("sunat_rhe", "FeeReceipt", "account_ruc", "Recibos por honorarios"),
        ("finance_analytics", "ManualEntry", "account_ruc", "Movimientos manuales"),
        ("finance_analytics", "InvoiceOverride", "account_ruc", "Correcciones de comprobantes"),
        ("finance_analytics", "FinanceAlert", "account_ruc", "Alertas financieras"),
    )),
    ("Declaraciones e impuestos", (
        ("sunat_declaraciones", "DeclaracionPresentada", "account_ruc", "Declaraciones presentadas"),
        ("sunat_declaraciones", "DeclaracionAnual", "account_ruc", "Declaraciones anuales"),
        ("sunat_itf", "ItfRecord", "taxpayer_id", "Registros ITF"),
        ("finance_analytics", "RentaProjection", "account_ruc", "Proyecciones de renta"),
    )),
    ("Conciliación y cobranzas", (
        ("reconciliation", "BankStatement", "account_ruc", "Estados de cuenta"),
        ("reconciliation", "BankMovement", "account_ruc", "Movimientos bancarios"),
        ("reconciliation", "ReconciliationRun", "account_ruc", "Corridas de conciliación"),
        ("reconciliation", "InvoiceSettlement", "account_ruc", "Liquidaciones de cobranza"),
        ("reconciliation", "ConsistencyScore", "account_ruc", "Puntajes de consistencia"),
    )),
    ("Cumplimiento y perfil SUNAT", (
        ("ruc_profile", "RucSnapshot", "ruc", "Fichas RUC"),
        ("ruc_profile", "RucTaxAffectation", "ruc", "Tributos afectos"),
        ("obligations", "CompanyObligation", "account_ruc", "Obligaciones"),
        ("obligations", "ComplianceSnapshot", "account_ruc", "Fotos de cumplimiento"),
        ("compliance_profile", "ComplianceRating", "taxpayer_id", "Calificaciones de cumplimiento"),
        ("remype", "RemypeCheck", "ruc", "Consultas REMYPE"),
        ("sunafil", "SunafilItem", "taxpayer_id", "Registros SUNAFIL"),
    )),
    ("Terceros", (
        ("suppliers", "Supplier", "account_ruc", "Proveedores vigilados"),
    )),
    ("Personas y planilla", (
        ("colaboradores", "Colaborador", "taxpayer_id", "Colaboradores"),
        ("colaboradores", "Contrato", "taxpayer_id", "Contratos"),
        ("payroll", "PayrollPeriod", "taxpayer_id", "Periodos de planilla"),
        ("payroll", "PayrollSettings", "taxpayer_id", "Parámetros de planilla"),
        ("afpnet", "AfpnetDeclaration", "taxpayer_id", "Declaraciones AFPnet"),
        ("afpnet", "AfpnetDebt", "taxpayer_id", "Deudas AFP"),
        ("afpnet", "AfpnetAffiliate", "taxpayer_id", "Afiliados AFP"),
    )),
    ("Contabilidad interna", (
        ("financials", "FinancialTransaction", "taxpayer_id", "Transacciones categorizadas"),
        ("financials", "TransactionCategory", "taxpayer_id", "Categorías propias"),
        ("financials", "CategorizationRule", "taxpayer_id", "Reglas de categorización"),
        ("financials", "FinancialSettings", "taxpayer_id", "Ajustes financieros"),
        ("financials", "ManualBalanceEntry", "taxpayer_id", "Saldos manuales"),
    )),
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    """La ficha de la empresa es el hub del admin: lo que cuelga por FK va
    como inline; lo que cuelga por RUC (account_ruc/taxpayer_id) no admite
    inline, así que el panorama lo enlaza ya filtrado con ?empresa=."""

    list_display = ("ruc", "name", "trade_name", "manual_sync_daily_limit")
    list_editable = ("manual_sync_daily_limit",)
    search_fields = ("ruc", "name", "trade_name")
    inlines = [MembershipInline, InvitationInline, SunatCredentialInline,
               SubscriptionInline, BusinessProfileInline]
    readonly_fields = ("panorama",)
    fieldsets = (
        (None, {"fields": ("ruc", "name", "trade_name",
                           ("tax_regime", "tax_regime_source", "tax_regime_checked_at"),
                           "manual_sync_daily_limit")}),
        ("Todo lo de esta empresa", {"fields": ("panorama",)}),
    )

    @admin.display(description="")
    def panorama(self, org: Organization) -> str:
        if not org.pk:
            return "Guarda la empresa para ver su panorama."
        bloques = []
        for titulo, filas in PANORAMA:
            items = []
            for app_label, modelo, campo, etiqueta in filas:
                model = apps.get_model(app_label, modelo)
                n = model.objects.filter(**{campo: org.ruc}).count()
                if model in admin.site._registry:
                    url = reverse(f"admin:{app_label}_{modelo.lower()}_changelist")
                    items.append(format_html(
                        '<li style="margin:2px 0"><a href="{}?empresa={}">{}</a>'
                        ' — <b>{}</b></li>',
                        url, org.ruc, etiqueta, n,
                    ))
                else:
                    items.append(format_html(
                        '<li style="margin:2px 0">{} — <b>{}</b></li>', etiqueta, n,
                    ))
            bloques.append(format_html(
                '<div style="break-inside:avoid;margin-bottom:12px">'
                '<h3 style="margin:0 0 4px">{}</h3>'
                '<ul style="margin:0;padding-left:16px">{}</ul></div>',
                titulo, format_html_join("", "{}", ((i,) for i in items)),
            ))
        return format_html(
            '<div style="columns:2;column-gap:32px;max-width:760px">{}</div>',
            format_html_join("", "{}", ((b,) for b in bloques)),
        )


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = ("organization", "sector", "offering", "primary_goal",
                    "business_age", "people_count", "completed_at")
    list_filter = ("sector", "offering", "primary_goal", "business_age")
    search_fields = ("organization__ruc", "organization__name")
    autocomplete_fields = ("organization",)


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "organization", "role", "status", "invited_by",
                    "accepted_user", "created_at")
    list_filter = ("status", "role")
    search_fields = ("email", "organization__ruc", "organization__name")
    autocomplete_fields = ("organization", "invited_by", "accepted_user")
    readonly_fields = ("token", "accepted_at", "accepted_user", "created_at", "updated_at")


@admin.register(SunatCredential)
class SunatCredentialAdmin(admin.ModelAdmin):
    """La clave cifrada no se muestra ni se edita desde el admin."""

    list_display = ("organization", "sol_username", "status", "uses_primary_user",
                    "last_verified_at")
    list_filter = ("status", "uses_primary_user")
    search_fields = ("organization__ruc", "sol_username")
    exclude = ("encrypted_password",)
    readonly_fields = ("last_verified_at", "last_error", "connected_by")


@admin.register(SunatAuthorization)
class SunatAuthorizationAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "sol_username", "version", "accepted_at", "ip_address", "revoked_at")
    list_filter = ("version", "revoked_at")
    search_fields = ("organization__ruc", "user__email", "sol_username", "ip_address")
    readonly_fields = tuple(f.name for f in SunatAuthorization._meta.fields)
