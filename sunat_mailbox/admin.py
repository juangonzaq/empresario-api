from django.contrib import admin

from core.admin import filtro_empresa

from .models import Attachment, Message


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    can_delete = False
    fields = ("file_name", "size_display", "page_count", "extraction_status", "text_preview")
    readonly_fields = fields

    @admin.display(description="Extracted text")
    def text_preview(self, obj: Attachment) -> str:
        if not obj.text_content:
            return obj.extraction_error or "—"
        return f"{obj.text_content[:200]}…"

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "sent_on", "taxpayer_id", "message_type", "short_subject",
        "is_read", "attachment_count",
    )
    list_filter = (filtro_empresa("taxpayer_id"), "message_type", "is_read", "is_urgent", "label_code")
    search_fields = ("subject", "message_code", "sender_name")
    date_hierarchy = "published_at"
    inlines = (AttachmentInline,)
    readonly_fields = ("list_payload", "detail_payload", "created_at", "updated_at")

    @admin.display(description="Subject", ordering="subject")
    def short_subject(self, obj: Message) -> str:
        return obj.subject[:90]

    def has_add_permission(self, request) -> bool:
        # Messages come from the scraper, never from the admin.
        return False
