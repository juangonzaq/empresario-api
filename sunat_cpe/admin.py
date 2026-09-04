from django.contrib import admin

from core.admin import filtro_empresa

from .models import ElectronicInvoice


@admin.register(ElectronicInvoice)
class ElectronicInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "issue_date", "direction", "document_class", "full_number",
        "issuer_ruc", "receiver_ruc", "total_amount", "currency",
        "status", "is_cancelled", "is_rejected", "references_document", "has_xml",
    )
    list_filter = (
        filtro_empresa("account_ruc"),
        "direction", "document_class", "period", "is_cancelled", "is_rejected",
        "status",
    )
    search_fields = (
        "full_number", "series", "number",
        "issuer_ruc", "issuer_name", "receiver_ruc", "receiver_name",
        "references_document",
    )
    date_hierarchy = "issue_date"
    readonly_fields = (
        "raw", "xml_content", "xml_sha256", "xml_downloaded_at", "xml_file",
        "last_seen_at", "created_at", "updated_at",
    )

    @admin.display(boolean=True, description="XML")
    def has_xml(self, obj: ElectronicInvoice) -> bool:
        return bool(obj.xml_content)

    def has_add_permission(self, request) -> bool:
        return False
