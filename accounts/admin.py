from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    BusinessProfile, Invitation, Membership, Organization, SunatCredential, User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "companies_summary",
                    "extra_company_seats", "email_verified_at", "is_staff")
    list_filter = ("is_staff", "is_superuser", "is_active")
    list_editable = ("extra_company_seats",)
    search_fields = ("email", "first_name", "last_name")
    readonly_fields = ("last_login", "created_at", "updated_at", "companies_summary")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Datos", {"fields": ("first_name", "last_name", "phone")}),
        # Asientos de empresa: los que incluye el plan del titular más estos
        # extra que le otorgas. `companies_summary` muestra el uso actual.
        ("Empresas y asientos", {"fields": ("extra_company_seats", "companies_summary")}),
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


from .models import SunatAuthorization  # noqa: E402


@admin.register(SunatAuthorization)
class SunatAuthorizationAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "sol_username", "version", "accepted_at", "ip_address", "revoked_at")
    list_filter = ("version", "revoked_at")
    search_fields = ("organization__ruc", "user__email", "sol_username", "ip_address")
    readonly_fields = tuple(f.name for f in SunatAuthorization._meta.fields)
