# Seeds the global category the fee-receipt ingest confirms against:
# «Honorarios profesionales», an administrative expense like its siblings.

from django.db import migrations


def seed(apps, schema_editor):
    StatementLine = apps.get_model("financials", "StatementLine")
    TransactionCategory = apps.get_model("financials", "TransactionCategory")

    line = StatementLine.objects.filter(
        taxpayer_id="", code="ADMIN_EXPENSES_LINE"
    ).first()
    if line is None:  # pragma: no cover — seed 0002 always creates it
        return
    TransactionCategory.objects.update_or_create(
        taxpayer_id="", code="PROFESSIONAL_FEES",
        defaults={
            "name": "Honorarios profesionales",
            "statement": "income_statement",
            "statement_line": line,
            "sign": -1,
            "applies_to": "purchases",
            "is_operating": True,
            "display_order": 85,
            "is_active": True,
        },
    )


def unseed(apps, schema_editor):
    TransactionCategory = apps.get_model("financials", "TransactionCategory")
    TransactionCategory.objects.filter(
        taxpayer_id="", code="PROFESSIONAL_FEES"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("financials", "0004_alter_financialtransaction_source"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
