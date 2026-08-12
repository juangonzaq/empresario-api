"""Régimen tributario declarado y token de suscripción al calendario.

El token es único y las empresas ya existentes no lo tienen. Se añade en tres
pasos en vez de uno: si se creara ya como ``unique`` con un default invocable,
Django evaluaría ese default **una sola vez** y escribiría el mismo valor en
todas las filas, y la restricción de unicidad reventaría en cuanto hubiera más
de una empresa.
"""

from django.db import migrations, models

import accounts.models


def poblar_tokens(apps, schema_editor):
    Organization = apps.get_model("accounts", "Organization")
    for organization in Organization.objects.filter(calendar_token=""):
        organization.calendar_token = accounts.models._nuevo_token_calendario()
        organization.save(update_fields=["calendar_token"])


def revertir(apps, schema_editor):
    """Nada que deshacer: la columna entera se va con la migración inversa."""


class Migration(migrations.Migration):

    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="tax_regime",
            field=models.CharField(
                blank=True,
                choices=[
                    ("RUS", "Nuevo RUS"),
                    ("RER", "Régimen Especial"),
                    ("RMT", "MYPE Tributario"),
                    ("RG", "Régimen General"),
                ],
                help_text="Vacío mientras la empresa no lo haya declarado.",
                max_length=3,
                verbose_name="régimen tributario",
            ),
        ),
        migrations.AddField(
            model_name="organization",
            name="calendar_token",
            field=models.CharField(default="", max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(poblar_tokens, revertir),
        migrations.AlterField(
            model_name="organization",
            name="calendar_token",
            field=models.CharField(
                default=accounts.models._nuevo_token_calendario,
                help_text="Credencial de la URL de suscripción al calendario.",
                max_length=64,
                unique=True,
            ),
        ),
    ]
