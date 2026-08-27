"""Programa la descarga mensual del padrón SSCO de SUNAT.

SUNAT lo republica a fin de mes (la «fecha de publicación» del Excel es el
último día). Se corre los días 28 a 31 a las 22:00: la tarea es idempotente y
barata —un Excel de unos cientos de filas—, así que repetirla tres o cuatro
noches seguidas cuesta menos que perderse la publicación por un mes corto.
"""

from django.conf import settings
from django.db import migrations

TASK = "suppliers.sync_ssco"
NAME = "Descargar padrón SSCO de SUNAT (fin de mes)"


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="22",
        day_of_week="*",
        day_of_month="28-31",
        month_of_year="*",
        timezone=settings.TIME_ZONE,
    )
    PeriodicTask.objects.get_or_create(
        task=TASK, defaults={"name": NAME, "crontab": crontab, "enabled": True}
    )


def unseed(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(task=TASK).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_seed_sunafil_schedule"),
        ("suppliers", "0003_sujetosincapacidadoperativa"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
