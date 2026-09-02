from django.contrib import admin

from core.admin import filtro_empresa

from .models import ItfRecord


@admin.register(ItfRecord)
class ItfRecordAdmin(admin.ModelAdmin):
    list_display = (
        "period", "taxpayer_id", "section", "declarant_name",
        "kind", "movement", "operation_code", "base_amount", "tax",
    )
    list_filter = (filtro_empresa("taxpayer_id"), "section", "period", "kind")
    search_fields = ("declarant_ruc", "declarant_name", "operation_code")
    readonly_fields = ("extra", "raw", "created_at", "updated_at")

    def has_add_permission(self, request) -> bool:
        # Records come from the scraper, never from the admin.
        return False
