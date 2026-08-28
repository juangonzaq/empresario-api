"""El pago a cuenta de renta declarado en el 621 entra al Estado de Resultados
como transacción con fuente propia."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("financials", "0005_seed_professional_fees"),
    ]

    operations = [
        migrations.AlterField(
            model_name="financialtransaction",
            name="source",
            field=models.CharField(choices=[
                ("sunat_sales", "Venta SUNAT"), ("sunat_purchases", "Compra SUNAT"),
                ("manual_sale", "Venta manual"), ("manual_purchase", "Compra manual"),
                ("payroll", "Planilla"), ("fee_receipt", "Recibo por honorarios"),
                ("sunat_declaration", "Declaración SUNAT"), ("adjustment", "Ajuste"),
            ], max_length=20),
        ),
    ]
