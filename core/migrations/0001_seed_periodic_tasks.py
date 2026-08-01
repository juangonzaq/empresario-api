"""Seed the Celery Beat schedule.

Schedules live in the database so they can be retimed or paused from the admin.
This only creates them; later edits made there are never overwritten.
"""

from django.conf import settings
from django.db import migrations

SCHEDULE = [
    {
        "name": "Check suppliers on SUNAT (daily)",
        "task": "suppliers.check_all",
        "hour": "7",
        "minute": "0",
    },
    {
        "name": "Scrape SUNAT mailbox (daily)",
        "task": "sunat_mailbox.scrape",
        # Half an hour after the supplier check so the two never overlap: the
        # mailbox task drives a real browser and is much heavier.
        "hour": "7",
        "minute": "30",
    },
]


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    for entry in SCHEDULE:
        crontab, _ = CrontabSchedule.objects.get_or_create(
            minute=entry["minute"],
            hour=entry["hour"],
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=settings.TIME_ZONE,
        )
        PeriodicTask.objects.get_or_create(
            task=entry["task"],
            defaults={"name": entry["name"], "crontab": crontab, "enabled": True},
        )


def unseed(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        task__in=[entry["task"] for entry in SCHEDULE]
    ).delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
