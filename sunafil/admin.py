from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import ItemKind, SunafilItem


@admin.register(SunafilItem)
class SunafilItemAdmin(admin.ModelAdmin):
    list_display = (
        "deposited_at", "kind", "short_subject", "record_number",
        "is_read", "deadline",
    )
    list_filter = ("kind", "is_read", "taxpayer_id", "status", "deposited_at")
    search_fields = ("subject", "record_number", "detail_text")
    date_hierarchy = "deposited_at"
    readonly_fields = tuple(field.name for field in SunafilItem._meta.fields) + (
        "rendered_detail",
    )
    fieldsets = (
        (None, {
            "fields": ("taxpayer_id", "kind", "subject", "category", "record_number",
                       "office", "status", "is_read"),
        }),
        ("Fechas", {
            "fields": ("deposited_at", "acknowledged_at", "notified_on",
                       "due_date", "deadline_days", "first_seen_on", "last_seen_on"),
        }),
        ("Detalle", {
            "classes": ("collapse",),
            "fields": ("detail_fetched_at", "detail_links", "detail_images",
                       "rendered_detail"),
        }),
        ("Crudo", {"classes": ("collapse",), "fields": ("row", "external_key")}),
    )

    @admin.display(description="Asunto", ordering="subject")
    def short_subject(self, obj: SunafilItem) -> str:
        return obj.subject[:80]

    @admin.display(description="Plazo", ordering="due_date")
    def deadline(self, obj: SunafilItem) -> str:
        if obj.kind == ItemKind.ORIENTATION or not obj.due_date:
            return "—"
        overdue = obj.due_date < timezone.localdate()
        return format_html(
            '<b style="color:{}">{}</b>',
            "#b3261e" if overdue else "#146c2e", obj.due_date,
        )

    @admin.display(description="Contenido")
    def rendered_detail(self, obj: SunafilItem) -> str:
        if not obj.detail_text:
            return "—"
        return format_html(
            "<div style='max-height:400px;overflow:auto'>{}</div>", obj.detail_text
        )

    def has_add_permission(self, request) -> bool:
        # Items come from the scraper, never from the admin.
        return False
