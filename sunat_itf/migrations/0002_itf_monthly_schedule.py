"""Register the monthly ITF scrape: 1st of each month, capturing last month.

Uses django_celery_beat's database scheduler (already configured in settings).
The task itself defaults period_end to the previous month, so no args are needed.
"""

from __future__ import annotations

from django.db import migrations

CRONTAB = {
    "minute": "0",
    "hour": "6",
    "day_of_month": "1",
    "month_of_year": "*",
    "day_of_week": "*",
}
TASK_NAME = "ITF: scrape previous month (monthly)"
TASK_PATH = "sunat_itf.scrape"


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
            # No args: the task computes the previous month on its own.
            "args": "[]",
            "description": (
                "Extrae la Consulta de ITF del mes recién cerrado, con el rango "
                "acumulado desde enero del año en curso."
            ),
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sunat_itf", "0001_initial"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
