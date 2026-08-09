"""Reemplaza los horarios de una sola empresa por dos que recorren todas.

Antes había un horario por fuente (``sunat_cpe.scrape_daily``,
``sunat_itf.scrape``, …) y cada tarea leía el RUC de ``settings.SUNAT_RUC``.
Con varias empresas eso sincronizaba a una sola y dejaba al resto sin datos.

Ahora Beat dispara ``sync.daily`` y ``sync.monthly``, que recorren las
empresas con SUNAT conectada y encolan un trabajo por cada una, escalonado.

Las tareas por app siguen existiendo para correrlas a mano contra una cuenta
concreta; lo que se retira es su horario.
"""

from __future__ import annotations

from django.db import migrations

# Horarios de la etapa monoempresa, que este cambio deja sin efecto.
SINGLE_TENANT_TASKS = [
    "sunat_cpe.scrape_daily",
    "sunat_itf.scrape",
    "sunat_mailbox.scrape",
    "sunafil.scrape",
    "ruc_profile.capture",
    "remype.refresh",
    "compliance_profile.scrape",
]

# De madrugada: a esa hora los portales de SUNAT responden mejor y nadie está
# mirando la pantalla mientras se llena.
DAILY = {
    "name": "Sincronización diaria de todas las empresas",
    "task": "sync.daily",
    "crontab": {"minute": "0", "hour": "4", "day_of_month": "*",
                "month_of_year": "*", "day_of_week": "*"},
    "description": (
        "Recorre las empresas con SUNAT conectada y encola su sincronización "
        "diaria: comprobantes de los meses recientes, buzón SUNAT, casilla "
        "SUNAFIL y recálculo de alertas. Las empresas van escalonadas."
    ),
}

# El día 2: el ITF del mes cerrado no está disponible el día 1 a primera hora.
MONTHLY = {
    "name": "Sincronización mensual de todas las empresas",
    "task": "sync.monthly",
    "crontab": {"minute": "30", "hour": "5", "day_of_month": "2",
                "month_of_year": "*", "day_of_week": "*"},
    "description": (
        "Lo que cambia mes a mes: movimientos bancarios (ITF) del mes "
        "cerrado, perfil de cumplimiento, ficha RUC y acreditación REMYPE."
    ),
}


def _timezone():
    from django.conf import settings

    return getattr(settings, "CELERY_TIMEZONE", "America/Lima")


def apply(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    PeriodicTask.objects.filter(task__in=SINGLE_TENANT_TASKS).delete()

    for spec in (DAILY, MONTHLY):
        crontab, _ = CrontabSchedule.objects.get_or_create(
            timezone=_timezone(), **spec["crontab"]
        )
        PeriodicTask.objects.update_or_create(
            name=spec["name"],
            defaults={
                "crontab": crontab,
                "interval": None,
                "task": spec["task"],
                "args": "[]",
                "description": spec["description"],
                "enabled": True,
            },
        )


def revert(apps, schema_editor):
    """Solo retira los horarios nuevos.

    No se recrean los de una sola empresa: volver a un scrapeo que ignora a
    todas las empresas menos una no es un estado al que convenga regresar.
    """
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name__in=[DAILY["name"], MONTHLY["name"]]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sync", "0002_syncjob_kind"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [migrations.RunPython(apply, revert)]
