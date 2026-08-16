"""A received credit note reduces the cost of purchases: it needs its own
category with a +1 sign over the cost line (the mirror of SALES_RETURNS),
so the auto-confirmation of purchases never inflates the cost."""

from __future__ import annotations

from django.db import migrations


def seed(apps, schema_editor):
    StatementLine = apps.get_model("financials", "StatementLine")
    TransactionCategory = apps.get_model("financials", "TransactionCategory")
    line = StatementLine.objects.get(
        taxpayer_id="", statement="income_statement", code="COST_OF_SALES_LINE"
    )
    TransactionCategory.objects.get_or_create(
        taxpayer_id="", code="PURCHASE_RETURNS",
        defaults={
            "name": "Devoluciones de compras",
            "statement": "income_statement",
            "statement_line": line,
            "sign": 1,  # reduces a negative line: adds back
            "applies_to": "purchases",
            "display_order": 55,
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("financials", "TransactionCategory").objects.filter(
        taxpayer_id="", code="PURCHASE_RETURNS"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("financials", "0002_seed_master_data")]
    operations = [migrations.RunPython(seed, unseed)]
