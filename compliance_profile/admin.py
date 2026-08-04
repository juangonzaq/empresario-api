from django.contrib import admin

from .models import ComplianceRating, ComplianceVariable


class ComplianceVariableInline(admin.TabularInline):
    model = ComplianceVariable
    extra = 0
    can_delete = False
    fields = ("variable_type", "code", "severity", "short_description", "record_count")
    readonly_fields = fields

    @admin.display(description="Description")
    def short_description(self, obj: ComplianceVariable) -> str:
        return obj.description[:120]

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ComplianceRating)
class ComplianceRatingAdmin(admin.ModelAdmin):
    list_display = (
        "period", "taxpayer_id", "rating", "preliminary_category",
        "is_current", "loaded_at", "detail_fetched_at",
    )
    list_filter = ("rating", "is_current", "taxpayer_id")
    search_fields = ("taxpayer_id", "period")
    inlines = (ComplianceVariableInline,)
    readonly_fields = ("header_payload", "detail_payload", "created_at", "updated_at")

    def has_add_permission(self, request) -> bool:
        # Ratings come from the scraper, never from the admin.
        return False
