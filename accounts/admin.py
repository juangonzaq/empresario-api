from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Membership, Organization, SunatCredential, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "email_verified_at", "is_staff")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "first_name", "last_name")
    readonly_fields = ("last_login", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Datos", {"fields": ("first_name", "last_name", "phone")}),
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


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("ruc", "name", "trade_name")
    search_fields = ("ruc", "name", "trade_name")
    inlines = [MembershipInline]


@admin.register(SunatCredential)
class SunatCredentialAdmin(admin.ModelAdmin):
    """La clave cifrada no se muestra ni se edita desde el admin."""

    list_display = ("organization", "sol_username", "status", "uses_primary_user",
                    "last_verified_at")
    list_filter = ("status", "uses_primary_user")
    search_fields = ("organization__ruc", "sol_username")
    exclude = ("encrypted_password",)
    readonly_fields = ("last_verified_at", "last_error", "connected_by")
