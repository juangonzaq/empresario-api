"""Schedule the REMYPE refresh.

REMYPE accreditation is granted once and rarely revoked, so this runs monthly, not
daily like the SUNAT checks. The task itself also skips RUCs whose stored check is
still fresh, so the monthly run is cheap.
"""

from django.conf import settings
from django.db import migrations

TASK = "remype.refresh"
NAME = "Refresh REMYPE accreditation (monthly)"


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
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
        ("core", "0001_seed_periodic_tasks"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
