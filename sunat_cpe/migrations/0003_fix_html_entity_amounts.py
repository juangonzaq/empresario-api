"""Repara los importes contaminados por entidades HTML.

SUNAT manda el símbolo del dólar como «&#36;», y el parser anterior solo
borraba lo que no fuera dígito: los caracteres 3 y 6 de la entidad quedaban
pegados al importe, así que «&#36;2,320.00» se guardó como 362320.00.

Los soles no se vieron afectados («S/» son letras, no una entidad), pero la
reparación se aplica a todo comprobante que conserve su registro crudo: se
vuelve a leer el importe con el parser ya corregido y se corrige también el
símbolo de moneda. Los que no tengan ``raw`` se dejan intactos.
"""

from __future__ import annotations

from django.db import migrations


def _reparse(apps, schema_editor):
    from sunat_cpe.services.parsing import clean_field, parse_amount

    Invoice = apps.get_model("sunat_cpe", "ElectronicInvoice")
    fixed = []
    for invoice in Invoice.objects.exclude(raw={}).only(
        "id", "raw", "total_amount", "currency_symbol"
    ):
        raw = invoice.raw or {}
        if not isinstance(raw, dict):
            continue
        amount = parse_amount(raw.get("importeTotalDesc"))
        symbol = clean_field(raw.get("codigoMonedaDesc"))
        if amount == invoice.total_amount and symbol == invoice.currency_symbol:
            continue
        invoice.total_amount = amount
        invoice.currency_symbol = symbol
        fixed.append(invoice)

    if fixed:
        Invoice.objects.bulk_update(fixed, ["total_amount", "currency_symbol"], batch_size=500)


def _noop(apps, schema_editor):
    """No se revierte: volver a los importes corruptos no aporta nada."""


class Migration(migrations.Migration):

    dependencies = [
        ("sunat_cpe", "0002_cpe_daily_schedule"),
    ]

    operations = [
        migrations.RunPython(_reparse, _noop),
    ]
