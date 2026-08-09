from django.contrib import admin

from .models import SyncJob


@admin.register(SyncJob)
class SyncJobAdmin(admin.ModelAdmin):
    list_display = ("organization", "status", "progress_pct", "started_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("organization__ruc", "organization__name")
    readonly_fields = ("steps", "started_at", "finished_at")
