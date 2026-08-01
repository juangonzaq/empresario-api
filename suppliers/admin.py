from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Supplier, SupplierCheck
from .services import SupplierMonitor

STATUS_COLOURS = {True: "#b3261e", False: "#146c2e"}


def standing_badge(status: str, condition: str, has_issue: bool) -> str:
    if not status:
        return format_html('<span style="color:#777">not checked yet</span>')
    return format_html(
        '<b style="color:{}">{}</b> / {}',
        STATUS_COLOURS[has_issue], status, condition or "—",
    )


class SupplierCheckInline(admin.TabularInline):
    model = SupplierCheck
    extra = 0
    can_delete = False
    fields = ("checked_on", "status", "condition", "changed", "succeeded", "error")
    readonly_fields = fields
    ordering = ("-checked_on",)
    max_num = 0
    verbose_name_plural = "Recent checks"

    def get_queryset(self, request):
        return super().get_queryset(request)[:30]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "ruc", "display_name", "standing", "is_tracked",
        "last_checked_at", "last_changed_at",
    )
    list_filter = ("has_issue", "is_tracked", "status", "condition")
    search_fields = ("ruc", "alias", "business_name", "trade_name")
    readonly_fields = (
        "business_name", "trade_name", "taxpayer_type", "fiscal_address",
        "economic_activities", "registered_on", "started_activities_on",
        "status", "condition", "has_issue", "last_checked_at", "last_changed_at",
        "last_error", "created_at", "updated_at",
    )
    fieldsets = (
        ("Registration", {"fields": ("ruc", "alias", "is_tracked", "notes")}),
        ("Current standing", {
            "fields": ("status", "condition", "has_issue",
                       "last_checked_at", "last_changed_at", "last_error"),
        }),
        ("SUNAT profile", {
            "classes": ("collapse",),
            "fields": ("business_name", "trade_name", "taxpayer_type",
                       "fiscal_address", "economic_activities",
                       "registered_on", "started_activities_on"),
        }),
        ("Audit", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )
    inlines = (SupplierCheckInline,)
    actions = ("check_now",)

    @admin.display(description="Standing", ordering="has_issue")
    def standing(self, obj: Supplier) -> str:
        return standing_badge(obj.status, obj.condition, obj.has_issue)

    @admin.display(description="Name", ordering="alias")
    def display_name(self, obj: Supplier) -> str:
        return obj.display_name

    @admin.action(description="Check selected suppliers on SUNAT now")
    def check_now(self, request, queryset) -> None:
        result = SupplierMonitor().run(suppliers=queryset)
        level = messages.WARNING if result.failed else messages.SUCCESS
        self.message_user(request, f"SUNAT check: {result}", level=level)


@admin.register(SupplierCheck)
class SupplierCheckAdmin(admin.ModelAdmin):
    list_display = (
        "checked_on", "supplier", "standing", "changed", "succeeded",
    )
    list_filter = ("checked_on", "has_issue", "changed", "succeeded", "status")
    search_fields = ("supplier__ruc", "supplier__alias", "supplier__business_name")
    date_hierarchy = "checked_on"
    readonly_fields = tuple(
        field.name for field in SupplierCheck._meta.fields
    )
    list_select_related = ("supplier",)

    @admin.display(description="Standing")
    def standing(self, obj: SupplierCheck) -> str:
        return standing_badge(obj.status, obj.condition, obj.has_issue)

    def has_add_permission(self, request) -> bool:
        # Checks are produced by the monitor, never by hand.
        return False
