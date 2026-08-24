"""Planes de arranque. Son datos, no código: se cambian desde el admin."""

from decimal import Decimal

from django.db import migrations


def crear_planes(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.get_or_create(code="mensual", defaults={
        "name": "Mensual", "description": "Todo Empresario, mes a mes.",
        "price": Decimal("99.90"), "currency": "PEN", "interval": "month", "sort_order": 1,
    })
    Plan.objects.get_or_create(code="anual", defaults={
        "name": "Anual", "description": "Dos meses gratis frente al mensual.",
        "price": Decimal("999.90"), "currency": "PEN", "interval": "year", "sort_order": 2,
    })


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(crear_planes, migrations.RunPython.noop)]
