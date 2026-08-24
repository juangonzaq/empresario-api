"""Código de referido por usuario y quién lo trajo.

En tres pasos porque el código es único: se añade vacío, se rellena para los
usuarios que ya existen y recién entonces se exige la unicidad."""

import secrets

from django.db import migrations, models
import django.db.models.deletion

ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def rellenar_codigos(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    usados = set()
    for user in User.objects.all().only("id", "referral_code"):
        while True:
            code = "".join(secrets.choice(ALFABETO) for _ in range(8))
            if code not in usados and not User.objects.filter(referral_code=code).exists():
                break
        usados.add(code)
        User.objects.filter(pk=user.pk).update(referral_code=code)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_organization_tax_regime_calendar_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="referral_code",
            field=models.CharField(blank=True, default="", max_length=12, verbose_name="código de referido"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="user",
            name="referred_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="referred_users", to="accounts.user"),
        ),
        migrations.RunPython(rellenar_codigos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="referral_code",
            field=models.CharField(blank=True, max_length=12, unique=True, verbose_name="código de referido"),
        ),
    ]
