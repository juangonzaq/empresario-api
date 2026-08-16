from django.contrib import admin

from .models import Colaborador, Contrato, Memorandum


@admin.register(Colaborador)
class ColaboradorAdmin(admin.ModelAdmin):
    list_display = (
        "full_name", "taxpayer_id", "document_number", "regimen",
        "monthly_salary", "salary_source", "is_active",
    )
    list_filter = ("regimen", "salary_source", "is_active", "afp")
    search_fields = ("full_name", "document_number", "cuspp", "taxpayer_id")
    readonly_fields = ("cuspp", "salary_period", "salary_updated_at",
                       "created_at", "updated_at")
    fieldsets = (
        ("Persona", {
            "fields": ("taxpayer_id", "full_name", "document_type",
                       "document_number", "position", "hired_on", "is_active"),
        }),
        ("Régimen", {"fields": ("regimen", "afp", "cuspp")}),
        ("Sueldo", {
            "fields": ("monthly_salary", "salary_source", "salary_period",
                       "salary_updated_at"),
        }),
        ("Notas", {"classes": ("collapse",), "fields": ("notes",)}),
        ("Auditoría", {"classes": ("collapse",),
                       "fields": ("created_at", "updated_at")}),
    )


@admin.register(Memorandum)
class MemorandumAdmin(admin.ModelAdmin):
    list_display = (
        "numero", "colaborador", "tipo", "fecha_emision", "entregado",
        "firmado", "taxpayer_id",
    )
    list_filter = ("tipo", "entregado", "firmado")
    search_fields = ("numero", "asunto", "colaborador__full_name", "taxpayer_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = (
        "colaborador", "tipo", "fecha_inicio", "fecha_fin", "renovar",
        "taxpayer_id",
    )
    list_filter = ("tipo", "renovar")
    search_fields = ("colaborador__full_name", "causa_objetiva", "taxpayer_id")
    readonly_fields = ("created_at", "updated_at")
