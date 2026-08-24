"""Programa el aviso diario de fin de prueba en Celery beat (DatabaseScheduler)."""

from django.db import migrations


def programar(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="9", day_of_week="*", day_of_month="*", month_of_year="*",
        timezone="America/Lima",
    )
    PeriodicTask.objects.get_or_create(
        name="billing: aviso de fin de prueba",
        defaults={"task": "billing.avisar_fin_de_prueba", "crontab": crontab},
    )


def quitar(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        name="billing: aviso de fin de prueba"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0004_aviso_fin_de_prueba"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]
    operations = [migrations.RunPython(programar, quitar)]
