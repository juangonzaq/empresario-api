from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.admin import filtro_empresa

from .models import RemypeCheck
from .services import RemypeLookupError, RemypeSynchronizer


@admin.register(RemypeCheck)
class RemypeCheckAdmin(admin.ModelAdmin):
    list_display = (
        "ruc", "business_name", "checked_on", "standing", "changed", "succeeded",
    )
    list_filter = (filtro_empresa("ruc"), "is_registered", "changed", "succeeded", "condition", "checked_on")
    search_fields = ("ruc", "business_name", "file_number")
    date_hierarchy = "checked_on"
    readonly_fields = tuple(field.name for field in RemypeCheck._meta.fields)
    actions = ("refresh_now",)

    @admin.display(description="REMYPE standing")
    def standing(self, obj: RemypeCheck) -> str:
        if not obj.succeeded:
            return mark_safe('<span style="color:#777">check failed</span>')
        if not obj.is_registered:
            return mark_safe('<b style="color:#b3261e">not registered</b>')
        colour = "#b3261e" if obj.deregistered_on else "#146c2e"
        return format_html(
            '<b style="color:{}">{}</b>', colour, obj.condition or "registered"
        )

    @admin.action(description="Look these RUCs up in REMYPE again")
    def refresh_now(self, request, queryset) -> None:
        rucs = sorted(set(queryset.values_list("ruc", flat=True)))
        try:
            # max_age_days=None: an explicit admin action always re-queries.
            result = RemypeSynchronizer().run(rucs, max_age_days=None)
        except RemypeLookupError as exc:
            self.message_user(request, f"REMYPE lookup failed: {exc}", messages.ERROR)
            return
        level = messages.WARNING if result.failed else messages.SUCCESS
        self.message_user(request, f"REMYPE refresh: {result}", level=level)

    def has_add_permission(self, request) -> bool:
        # Checks come from the lookup service, never from the admin.
        return False
