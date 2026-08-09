"""Arranque y ejecución del trabajo de sincronización de una empresa."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils import timezone

from accounts.models import Organization, SunatConnectionStatus

from .models import STALE_MESSAGE, JobKind, JobStatus, StepStatus, SyncJob
from .sources import Cadence, LoginFailed, initial_steps, sources_for

logger = logging.getLogger(__name__)


class NotConnected(Exception):
    """La empresa no tiene credenciales SUNAT guardadas."""


@dataclass(frozen=True)
class Credentials:
    ruc: str
    username: str
    password: str


def credentials_for(organization: Organization) -> Credentials:
    credential = getattr(organization, "sunat_credential", None)
    if credential is None or not credential.encrypted_password:
        raise NotConnected(
            f"La empresa {organization.ruc} no tiene SUNAT conectada."
        )
    return Credentials(
        ruc=organization.ruc,
        username=credential.sol_username,
        password=credential.password,
    )


def start_sync(
    organization: Organization,
    kind: str = JobKind.INITIAL,
    requested_by=None,
) -> SyncJob:
    """Encola una sincronización. Si ya hay una en marcha, la devuelve en lugar
    de encolar otra: dos scrapeos simultáneos con el mismo usuario SOL se
    estorban entre sí, y SUNAT admite una sesión por usuario."""
    reclaim_stale(organization)
    running = SyncJob.objects.filter(organization=organization).active().first()
    if running is not None:
        return running

    job = SyncJob.objects.create(
        organization=organization,
        kind=kind,
        requested_by=requested_by,
        steps=initial_steps(kind),
    )
    from .tasks import run_sync_job

    try:
        run_sync_job.apply_async((str(job.id),))
    except Exception:  # noqa: BLE001 — broker caído u ocupado
        # Encolar puede fallar (Redis abajo) y eso NO debe tumbar la operación
        # que lo pidió: la credencial ya quedó guardada y el trabajo queda en
        # cola para relanzarse desde la interfaz. Perder la conexión recién
        # configurada por un hipo del broker sería mucho peor.
        logger.exception(
            "No se pudo encolar la sincronización de %s; queda pendiente",
            organization.ruc,
        )
    return job


def execute(job: SyncJob) -> SyncJob:
    """Corre los pasos en serie. Aislado de Celery para poder probarlo."""
    organization = job.organization
    sources = sources_for(job.kind)
    job.start()

    try:
        credentials = credentials_for(organization)
    except NotConnected as exc:
        for source in sources:
            job.mark_step(source.key, StepStatus.SKIPPED, "Sin credenciales")
        job.finish(error=str(exc))
        return job

    credential = organization.sunat_credential
    fatal = ""

    for source in sources:
        if fatal and source.needs_sol:
            job.mark_step(source.key, StepStatus.SKIPPED, "No se pudo entrar a SUNAT")
            continue

        job.mark_step(source.key, StepStatus.RUNNING)
        try:
            result = source.run(credentials, job.kind)
        except LoginFailed as exc:
            # Credenciales malas: no tiene sentido seguir intentando con el
            # resto de portales, y el usuario debe volver a conectarse.
            fatal = str(exc)
            credential.status = SunatConnectionStatus.INVALID
            credential.last_error = fatal[:500]
            credential.save(update_fields=["status", "last_error", "updated_at"])
            job.mark_step(source.key, StepStatus.FAILED, "Credenciales rechazadas")
            logger.warning("Login SUNAT rechazado para %s", organization.ruc)
        except Exception as exc:  # noqa: BLE001 — un paso caído no tumba el resto
            logger.exception("Falló el paso %s de %s", source.key, organization.ruc)
            job.mark_step(source.key, StepStatus.FAILED, str(exc)[:300])
        else:
            detail = ", ".join(f"{k}: {v}" for k, v in (result or {}).items())
            job.mark_step(source.key, StepStatus.DONE, detail)
            # El primer portal que responde confirma que la clave sirve.
            if source.needs_sol and credential.status != SunatConnectionStatus.CONNECTED:
                credential.status = SunatConnectionStatus.CONNECTED
                credential.last_verified_at = timezone.now()
                credential.last_error = ""
                credential.save(update_fields=[
                    "status", "last_verified_at", "last_error", "updated_at",
                ])

    job.finish(error=fatal)
    # Los datos que alimentan el panel acaban de cambiar.
    from finance_analytics.cache import invalidate

    invalidate(organization.ruc)
    logger.info(
        "Sincronización de %s terminada: %s", organization.ruc, job.status
    )
    return job


def reclaim_stale(organization: Organization | None = None) -> int:
    """Cierra los trabajos abandonados para que no bloqueen a su empresa.

    Se marcan como fallidos con un texto que se entiende, en vez de dejarlos
    girando: la pantalla debe decir «quedó a medias, vuelve a lanzarla», no
    consultar un avance que no va a moverse nunca.
    """
    stale = SyncJob.objects.stale()
    if organization is not None:
        stale = stale.filter(organization=organization)

    count = 0
    for job in stale:
        for step in job.steps:
            if step.get("status") in (StepStatus.PENDING, StepStatus.RUNNING):
                step["status"] = StepStatus.SKIPPED
        job.status = JobStatus.FAILED
        job.error = STALE_MESSAGE
        job.finished_at = timezone.now()
        job.save(update_fields=["steps", "status", "error", "finished_at", "updated_at"])
        count += 1
    if count:
        logger.warning("Se cerraron %s sincronizaciones abandonadas", count)
    return count


def latest_job(organization: Organization) -> SyncJob | None:
    return SyncJob.objects.filter(organization=organization).first()
