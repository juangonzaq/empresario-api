"""Schedule the monthly full RUC profile capture.

This is ten requests per RUC and most of what it returns moves slowly, so it runs
monthly. The daily ``suppliers.check_all`` still watches estado/condición.
"""

from django.conf import settings
from django.db import migrations

TASK = "ruc_profile.capture"
NAME = "Capture full SUNAT RUC profiles (monthly)"


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="30",
        hour="8",
        day_of_week="*",
        day_of_month="1",
        month_of_year="*",
        timezone=settings.TIME_ZONE,
    )
    PeriodicTask.objects.get_or_create(
        task=TASK, defaults={"name": NAME, "crontab": crontab, "enabled": True}
    )


def unseed(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        task=TASK
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_seed_remype_schedule"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
