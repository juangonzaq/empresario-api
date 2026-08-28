from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance_analytics", "0006_renta_projection"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoiceextract", name="due_date",
            field=models.DateField(blank=True, help_text="cbc:DueDate.", null=True),
        ),
        migrations.AddField(
            model_name="invoiceextract", name="order_reference",
            field=models.CharField(blank=True, help_text="Orden de compra (cac:OrderReference).", max_length=60),
        ),
        migrations.AlterField(
            model_name="invoiceextract", name="items",
            field=models.JSONField(blank=True, default=list, help_text='Líneas: [{"code", "description", "quantity", "unit", "unit_value", "unit_price", "amount", "tax", "affectation"}].'),
        ),
    ]
