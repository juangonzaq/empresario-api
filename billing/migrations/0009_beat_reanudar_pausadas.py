"""Reanuda cada hora las suscripciones pausadas por un mes gratis (Celery beat)."""

from django.db import migrations


def programar(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="15", hour="*", day_of_week="*", day_of_month="*", month_of_year="*",
        timezone="America/Lima",
    )
    PeriodicTask.objects.get_or_create(
        name="billing: reanudar suscripciones pausadas",
        defaults={"task": "billing.reanudar_suscripciones_pausadas", "crontab": crontab},
    )


def quitar(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        name="billing: reanudar suscripciones pausadas"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0008_subscription_paused_until"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]
    operations = [migrations.RunPython(programar, quitar)]
