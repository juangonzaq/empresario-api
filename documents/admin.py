from django.contrib import admin

from core.admin import filtro_empresa

from .models import DocumentExport


@admin.register(DocumentExport)
class DocumentExportAdmin(admin.ModelAdmin):
    """Bitácora: quién se llevó qué y cuándo. Nada se edita a mano."""

    list_display = (
        "created_at", "account_ruc", "user", "label", "document_count",
        "downloaded_at", "attempts", "zip_bytes",
    )
    list_filter = (filtro_empresa("account_ruc"), "source")
    search_fields = ("account_ruc", "label", "user__email")
    readonly_fields = [f.name for f in DocumentExport._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
