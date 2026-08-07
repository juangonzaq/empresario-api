from django.contrib import admin

from .models import Case, CaseEvent, MessageAnalysis, VigiaMessage


@admin.register(VigiaMessage)
class VigiaMessageAdmin(admin.ModelAdmin):
    list_display = ("role", "content", "created_at")
    list_filter = ("role",)
    search_fields = ("content",)


@admin.register(MessageAnalysis)
class MessageAnalysisAdmin(admin.ModelAdmin):
    list_display = ("message", "status", "priority", "requires_action", "confidence")
    list_filter = ("status", "priority", "requires_action", "confidence")
    search_fields = ("message__subject", "comm_type", "summary")
    readonly_fields = ("raw_response", "fingerprint")


class CaseEventInline(admin.TabularInline):
    model = CaseEvent
    extra = 0
    readonly_fields = ("actor", "kind", "description", "created_at")


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("title", "risk", "status", "responsible", "deadline")
    list_filter = ("risk", "status", "requires_decision")
    search_fields = ("title", "summary", "group_key")
    inlines = [CaseEventInline]
