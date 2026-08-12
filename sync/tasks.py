"""Ejecución en cola de las sincronizaciones.

Dos niveles:

* ``run_sync_job`` — corre el trabajo de UNA empresa.
* ``sync_all`` — lo que dispara Celery Beat: recorre las empresas conectadas
  y encola una por una.

Antes había un horario por fuente apuntando al RUC del ``.env``. Con varias
empresas eso solo sincronizaba a una y dejaba al resto sin datos.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from celery import shared_task

from .models import JobKind, SyncJob

logger = logging.getLogger(__name__)

# Cuántas empresas caben en un día, con la concurrencia del worker de la cola
# «scraping». Solo se usa para avisar cuando el reparto no entra en la ventana;
# el límite real lo pone `-c` al arrancar ese worker.
SYNC_CONCURRENCY = int(os.getenv("SYNC_CONCURRENCY", "4"))
MINUTOS_POR_EMPRESA = int(os.getenv("SYNC_MINUTES_PER_ORG", "5"))


@shared_task(
    name="sync.run_job",
    # Cola propia: se limita con la concurrencia de su worker sin frenar el
    # resto de tareas. Ver CELERY_TASK_ROUTES en settings.
    queue="scraping",
    # Los scrapeos abren navegadores y recorren varios portales: el trabajo
    # completo puede tardar. No se reintenta solo — cada paso ya registra su
    # propio fallo y el usuario puede volver a lanzarlo desde la interfaz.
    time_limit=60 * 120,
    soft_time_limit=60 * 115,
)
def run_sync_job(job_id: str) -> dict[str, Any]:
    from .services import execute

    job = SyncJob.objects.filter(pk=job_id).select_related("organization").first()
    if job is None:
        logger.warning("Trabajo de sincronización %s ya no existe", job_id)
        return {"status": "desaparecido"}
    execute(job)
    return {
        "status": job.status,
        "kind": job.kind,
        "ruc": job.organization.ruc,
        "pasos_ok": sum(1 for s in job.steps if s.get("status") == "completo"),
    }


@shared_task(
    name="sync.run_step",
    # Misma cola que el trabajo completo: un reintento abre el mismo navegador
    # contra el mismo portal, y debe competir por los mismos huecos.
    queue="scraping",
    time_limit=60 * 120,
    soft_time_limit=60 * 115,
)
def run_sync_step(job_id: str, step_key: str) -> dict[str, Any]:
    """Relanza un paso suelto de un trabajo que ya terminó."""
    from .services import execute_step

    job = SyncJob.objects.filter(pk=job_id).select_related("organization").first()
    if job is None:
        logger.warning("Trabajo de sincronización %s ya no existe", job_id)
        return {"status": "desaparecido"}
    execute_step(job, step_key)
    step = next((s for s in job.steps if s.get("key") == step_key), {})
    return {
        "status": job.status,
        "paso": step_key,
        "paso_status": step.get("status", ""),
        "ruc": job.organization.ruc,
    }


@shared_task(name="sync.sync_all")
def sync_all(kind: str = JobKind.DAILY) -> dict[str, Any]:
    """Encola la sincronización de todas las empresas conectadas.

    Se salta a propósito:

    * las que no tienen credenciales guardadas;
    * las que las tienen **rechazadas** — reintentar cada noche con una clave
      que SUNAT ya rechazó no la va a arreglar, y sí puede acabar bloqueando
      el usuario SOL del cliente. Vuelven a entrar en cuanto alguien las
      corrige desde la interfaz;
    * las que ya tienen un trabajo en marcha.
    """
    from .services import reclaim_stale, start_sync

    # Antes de repartir trabajo, se liberan las empresas que quedaron
    # atascadas: si no, se saltarían indefinidamente.
    reclaim_stale()

    credentials = _syncable_credentials()

    queued, skipped = [], []
    for credential in credentials:
        organization = credential.organization
        if SyncJob.objects.filter(organization=organization).active().exists():
            skipped.append(organization.ruc)
            continue
        # Se encolan todas de golpe. Quien limita cuántas corren a la vez es la
        # concurrencia del worker de la cola «scraping», no un retardo por
        # empresa: con un retardo lineal de 2 minutos, mil empresas tardaban
        # 33 horas en arrancar y el reparto diario no llegaba a terminar.
        start_sync(organization, kind=kind)
        queued.append(organization.ruc)

    _avisar_si_no_entra(len(queued), kind)

    rejected = _rejected_count()
    logger.info(
        "Sincronización %s: %s encoladas, %s en curso, %s con credenciales "
        "rechazadas", kind, len(queued), len(skipped), rejected,
    )
    return {
        "kind": str(kind),
        "encoladas": len(queued),
        "omitidas_en_curso": len(skipped),
        # Se reporta para que no pase inadvertido: son empresas que llevan sin
        # sincronizar desde que su clave dejó de servir.
        "con_credenciales_rechazadas": rejected,
    }


def _avisar_si_no_entra(total: int, kind: str) -> None:
    """Avisa cuando el trabajo repartido no cabe en su propia ventana.

    Es el fallo que no se ve: las últimas empresas de la lista simplemente no
    se sincronizan, y nadie se entera hasta que un cliente pregunta por qué
    sus datos son de la semana pasada.
    """
    if not total:
        return
    horas = total * MINUTOS_POR_EMPRESA / max(SYNC_CONCURRENCY, 1) / 60
    ventana = 24 if kind == JobKind.DAILY else 24 * 28
    if horas > ventana * 0.8:
        logger.error(
            "El reparto %s necesita ~%.1f h con concurrencia %s y no entra en "
            "su ventana de %s h. Sube SYNC_CONCURRENCY y la concurrencia del "
            "worker de la cola «scraping», o reparte en más tandas.",
            kind, horas, SYNC_CONCURRENCY, ventana,
        )


def _rejected_count() -> int:
    from accounts.models import SunatConnectionStatus, SunatCredential

    return SunatCredential.objects.filter(
        status=SunatConnectionStatus.INVALID
    ).count()


def _syncable_credentials():
    from accounts.models import SunatConnectionStatus, SunatCredential

    return list(
        SunatCredential.objects.filter(
            status__in=[
                SunatConnectionStatus.CONNECTED,
                SunatConnectionStatus.PENDING,
            ],
        )
        .exclude(encrypted_password="")
        .select_related("organization")
        .order_by("organization__ruc")
    )


@shared_task(name="sync.daily")
def sync_daily() -> dict[str, Any]:
    return sync_all(JobKind.DAILY)


@shared_task(name="sync.monthly")
def sync_monthly() -> dict[str, Any]:
    return sync_all(JobKind.MONTHLY)
