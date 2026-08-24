from django.contrib import admin

from .models import FeeReceipt


@admin.register(FeeReceipt)
class FeeReceiptAdmin(admin.ModelAdmin):
    list_display = (
        "account_ruc", "full_number", "issuer_name", "issue_date",
        "gross_amount", "income_tax_withheld", "is_reverted",
    )
    list_filter = ("is_reverted", "period")
    search_fields = ("issuer_name", "issuer_doc", "full_number")
