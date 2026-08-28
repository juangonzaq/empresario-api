"""Fuentes para lo que SUNAT ya sabe y los comprobantes no muestran: la
planilla declarada en la PLAME, la depreciación y el impuesto del ejercicio
del 710, y las multas pagadas con boletas. Más la categoría de multas."""

from django.db import migrations, models

SOURCES = [
    ("sunat_sales", "Venta SUNAT"), ("sunat_purchases", "Compra SUNAT"),
    ("manual_sale", "Venta manual"), ("manual_purchase", "Compra manual"),
    ("payroll", "Planilla"), ("fee_receipt", "Recibo por honorarios"),
    ("sunat_declaration", "Declaración SUNAT"), ("sunat_plame", "Planilla declarada (PLAME)"),
    ("sunat_annual", "DJ anual de renta"), ("adjustment", "Ajuste"),
]


def seed(apps, schema_editor):
    StatementLine = apps.get_model("financials", "StatementLine")
    TransactionCategory = apps.get_model("financials", "TransactionCategory")
    line = StatementLine.objects.filter(taxpayer_id="", code="FINANCIAL_EXPENSES_LINE").first()
    if line is None:  # pragma: no cover
        return
    TransactionCategory.objects.update_or_create(
        taxpayer_id="", code="SUNAT_PENALTIES",
        defaults={
            "name": "Multas e intereses SUNAT", "statement": "income_statement",
            "statement_line": line, "sign": -1, "applies_to": "purchases",
            "is_operating": False, "display_order": 145, "is_active": True,
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("financials", "TransactionCategory").objects.filter(taxpayer_id="", code="SUNAT_PENALTIES").delete()


class Migration(migrations.Migration):
    dependencies = [("financials", "0006_source_sunat_declaration")]
    operations = [
        migrations.AlterField(
            model_name="financialtransaction", name="source",
            field=models.CharField(choices=SOURCES, max_length=20),
        ),
        migrations.RunPython(seed, unseed),
    ]
