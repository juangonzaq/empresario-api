"""Register the daily CPE cross-check.

Runs every day, re-querying the recent months across all comprobante types so
newly issued documents (a nota de crédito offsetting a factura, a received
invoice, etc.) are captured. Uses django_celery_beat's database scheduler.
"""

from __future__ import annotations

from django.db import migrations

CRONTAB = {
    "minute": "0",
    "hour": "5",
    "day_of_month": "*",
    "month_of_year": "*",
    "day_of_week": "*",
}
TASK_NAME = "CPE: daily cross-check of emitted/received comprobantes + XML"
TASK_PATH = "sunat_cpe.scrape_daily"


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    from django.conf import settings

    crontab, _ = CrontabSchedule.objects.get_or_create(
        timezone=getattr(settings, "CELERY_TIMEZONE", "America/Lima"), **CRONTAB
    )
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={
            "crontab": crontab,
            "task": TASK_PATH,
            "kwargs": '{"window_months": 2}',
            "description": (
                "Consulta diaria de comprobantes (facturas, NC y ND, emitidas y "
                "recibidas) de los últimos meses; descarga los XML nuevos. El "
                "backfill histórico completo se lanza aparte con scrape_cpe --full."
            ),
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sunat_cpe", "0001_initial"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
