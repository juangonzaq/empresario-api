"""Schedule the daily SUNAFIL casilla scrape.

Daily rather than monthly: requirements and notifications carry response deadlines,
so a new one must surface the same day it is deposited.
"""

from django.conf import settings
from django.db import migrations

TASK = "sunafil.scrape"
NAME = "Scrape SUNAFIL casilla (daily)"


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="9",
        day_of_week="*",
        day_of_month="*",
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
        ("core", "0003_seed_ruc_profile_schedule"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
