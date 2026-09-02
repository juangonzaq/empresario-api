"""«Qué vendes» pasa a ser lista, como rubros y objetivos: un negocio puede
vender productos Y servicios. El «Un poco de todo» (mixed) deja de existir como
opción —era el parche para no poder marcar varios— y los perfiles que lo tenían
pasan a [products, services]. El valor único anterior queda como columna espejo
(primer elemento), igual que sector/primary_goal."""

from django.db import migrations, models


def forwards(apps, schema_editor):
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    for profile in BusinessProfile.objects.all():
        if profile.offering == "mixed":
            profile.offerings = ["products", "services"]
        elif profile.offering:
            profile.offerings = [profile.offering]
        else:
            profile.offerings = []
        profile.offering = profile.offerings[0] if profile.offerings else ""
        profile.save(update_fields=["offerings", "offering"])


def backwards(apps, schema_editor):
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    for profile in BusinessProfile.objects.all():
        offerings = profile.offerings or []
        if {"products", "services"} <= set(offerings):
            profile.offering = "mixed"
        else:
            profile.offering = offerings[0] if offerings else ""
        profile.save(update_fields=["offering"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_businessprofile_has_payroll"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessprofile", name="offerings",
            field=models.JSONField(blank=True, default=list, verbose_name="qué vende"),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="businessprofile", name="offering",
            field=models.CharField(blank=True, choices=[("products", "Productos"), ("services", "Servicios"), ("food", "Comida o bebidas"), ("unsure", "No estoy seguro")], editable=False, max_length=15),
        ),
    ]
