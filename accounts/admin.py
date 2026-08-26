from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from billing.models import Referral, ReferralReward
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


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("ruc", "name", "trade_name", "manual_sync_daily_limit")
    list_editable = ("manual_sync_daily_limit",)
    search_fields = ("ruc", "name", "trade_name")
    inlines = [MembershipInline, InvitationInline]


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
