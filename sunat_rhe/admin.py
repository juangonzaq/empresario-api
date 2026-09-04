from django.contrib import admin

from core.admin import filtro_empresa

from .models import FeeReceipt


@admin.register(FeeReceipt)
class FeeReceiptAdmin(admin.ModelAdmin):
    list_display = (
        "account_ruc", "full_number", "issuer_name", "issue_date",
        "gross_amount", "income_tax_withheld", "is_reverted",
    )
    list_filter = (filtro_empresa("account_ruc"), "is_reverted", "period")
    search_fields = ("issuer_name", "issuer_doc", "full_number")
    # El archivo lo escribe el scraping o la subida del PDF, nunca el admin.
    readonly_fields = ("file",)
