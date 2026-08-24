from django.contrib import admin
from django.utils import timezone

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "ruc", "company", "source", "created_at", "contacted_at")
    list_filter = ("source", "contacted_at", "created_at")
    search_fields = ("name", "email", "phone", "ruc", "company")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")
    actions = ("mark_contacted",)

    @admin.action(description="Marcar como contactados")
    def mark_contacted(self, request, queryset):
        queryset.filter(contacted_at__isnull=True).update(contacted_at=timezone.now())
