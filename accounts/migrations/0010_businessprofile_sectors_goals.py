"""Rubros y objetivos pasan a ser listas: un negocio puede identificarse con
varios rubros y querer mejorar varias cosas. El valor único anterior se
conserva como primer elemento (y como columna espejo para el admin)."""

from django.db import migrations, models


def forwards(apps, schema_editor):
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    for profile in BusinessProfile.objects.all():
        profile.sectors = [profile.sector] if profile.sector else []
        profile.goals = [profile.primary_goal] if profile.primary_goal else []
        profile.save(update_fields=["sectors", "goals"])


def backwards(apps, schema_editor):
    BusinessProfile = apps.get_model("accounts", "BusinessProfile")
    for profile in BusinessProfile.objects.all():
        profile.sector = (profile.sectors or [""])[0]
        profile.primary_goal = (profile.goals or [""])[0]
        profile.save(update_fields=["sector", "primary_goal"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_businessprofile_has_premises_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessprofile", name="sectors",
            field=models.JSONField(blank=True, default=list, verbose_name="rubros"),
        ),
        migrations.AddField(
            model_name="businessprofile", name="goals",
            field=models.JSONField(blank=True, default=list, verbose_name="objetivos"),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="businessprofile", name="sector",
            field=models.CharField(blank=True, choices=[("commerce", "Comercio"), ("services", "Servicios"), ("manufacturing", "Manufactura"), ("food", "Alimentos"), ("construction", "Construcción"), ("other", "Otro")], editable=False, max_length=15),
        ),
        migrations.AlterField(
            model_name="businessprofile", name="primary_goal",
            field=models.CharField(blank=True, choices=[("order_numbers", "Ordenar mis números"), ("tax_ready", "Prepararme para impuestos"), ("cashflow", "No quedarme sin caja"), ("growth", "Crecer con más claridad"), ("profitability", "Saber si vendo con ganancia")], editable=False, max_length=15),
        ),
    ]
