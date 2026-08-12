"""Arranque y ejecución del trabajo de sincronización de una empresa."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils import timezone

from accounts.models import Organization, SunatConnectionStatus

from .models import STALE_MESSAGE, JobKind, JobStatus, StepStatus, SyncJob
from .sources import (
    LoginFailed, SOURCES_BY_KEY, Source, initial_steps, sources_for,
)

logger = logging.getLogger(__name__)


class NotConnected(Exception):
    """La empresa no tiene credenciales SUNAT guardadas."""


class CannotRetry(Exception):
    """El paso que se quiere relanzar no está en condiciones de correr."""


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


def _run_source(job: SyncJob, source: Source, credentials: Credentials) -> str:
    """Corre una fuente y deja su resultado escrito en el paso del trabajo.

    Devuelve el motivo por el que no se pudo entrar a SUNAT, o cadena vacía.
    Ese motivo es fatal para el resto del trabajo —no tiene sentido insistir
    con una clave que el portal acaba de rechazar— pero quien decide qué hacer
    con él es el llamador, porque en un reintento de un paso suelto no hay
    resto que cortar.
    """
    organization = job.organization
    credential = organization.sunat_credential

    job.mark_step(source.key, StepStatus.RUNNING)
    try:
        result = source.run(credentials, job.kind)
    except LoginFailed as exc:
        # Credenciales malas: el usuario debe volver a conectarse.
        credential.status = SunatConnectionStatus.INVALID
        credential.last_error = str(exc)[:500]
        credential.save(update_fields=["status", "last_error", "updated_at"])
        job.mark_step(source.key, StepStatus.FAILED, "Credenciales rechazadas")
        logger.warning("Login SUNAT rechazado para %s", organization.ruc)
        return str(exc)
    except Exception as exc:  # noqa: BLE001 — un paso caído no tumba el resto
        logger.exception("Falló el paso %s de %s", source.key, organization.ruc)
        job.mark_step(source.key, StepStatus.FAILED, str(exc)[:300])
        return ""

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
    return ""


def _wrap_up(job: SyncJob, error: str) -> SyncJob:
    """Cierra el trabajo y tira el caché de lo que acaba de cambiar."""
    job.finish(error=error)
    # Los datos que alimentan el panel acaban de cambiar.
    from finance_analytics.cache import invalidate

    invalidate(job.organization.ruc)
    logger.info(
        "Sincronización de %s terminada: %s", job.organization.ruc, job.status
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

    fatal = ""
    for source in sources:
        if fatal and source.needs_sol:
            job.mark_step(source.key, StepStatus.SKIPPED, "No se pudo entrar a SUNAT")
            continue
        fatal = _run_source(job, source, credentials) or fatal

    return _wrap_up(job, fatal)


def execute_step(job: SyncJob, key: str) -> SyncJob:
    """Vuelve a correr UN paso del trabajo y recalcula su estado.

    Los demás pasos conservan lo que ya habían traído, así que reintentar el
    buzón después de arreglar la clave no obliga a repetir el histórico de
    comprobantes, que es lo que tarda de verdad.
    """
    source = SOURCES_BY_KEY.get(key)
    if source is None:
        raise CannotRetry(f"El paso «{key}» no existe.")

    job.start()
    try:
        credentials = credentials_for(job.organization)
    except NotConnected as exc:
        job.mark_step(key, StepStatus.SKIPPED, "Sin credenciales")
        job.finish(error=str(exc))
        return job

    fatal = _run_source(job, source, credentials)
    return _wrap_up(job, fatal)


def _job_para_un_paso(organization: Organization) -> SyncJob:
    """El trabajo sobre el que se va a mover un solo paso, ya validado.

    Común a reintentar y a relanzar: en ambos casos se opera sobre el último
    trabajo de la empresa, porque es la lista de pasos que el usuario está
    mirando, y en ambos hay que negarse si ya hay algo en marcha —SUNAT admite
    una sesión por usuario SOL—.
    """
    reclaim_stale(organization)
    job = latest_job(organization)
    if job is None:
        raise CannotRetry(
            "Todavía no has sincronizado esta empresa. Lanza una "
            "sincronización completa la primera vez."
        )
    if job.is_unfinished:
        raise CannotRetry(
            "Hay una sincronización en marcha; espera a que termine."
        )
    return job


def _encolar_paso(job: SyncJob, key: str, requested_by, accion: str) -> SyncJob:
    if requested_by is not None:
        job.requested_by = requested_by
        job.save(update_fields=["requested_by", "updated_at"])
    job.reopen_step(key)

    from .tasks import run_sync_step

    try:
        run_sync_step.apply_async((str(job.id), key))
    except Exception:  # noqa: BLE001 — broker caído u ocupado
        # Igual que en `start_sync`: el paso queda en cola y se puede volver a
        # pulsar; tumbar la petición no arreglaría el broker.
        logger.exception(
            "No se pudo encolar %s del paso %s de %s", accion, key, job.organization.ruc,
        )
    return job


def retry_step(
    organization: Organization, key: str, requested_by=None
) -> SyncJob:
    """Encola de nuevo un paso que falló, sin repetir los que sí funcionaron.

    Se apoya en el último trabajo de la empresa en lugar de crear uno nuevo:
    el usuario está mirando esa lista de pasos y espera ver moverse esa misma
    línea, no perder de vista el resultado de las otras nueve.
    """
    job = _job_para_un_paso(organization)
    if not job.can_retry(key):
        raise CannotRetry("Ese paso no quedó fallido, no hay nada que reintentar.")
    return _encolar_paso(job, key, requested_by, "el reintento")


def run_source(
    organization: Organization, key: str, requested_by=None
) -> SyncJob:
    """Vuelve a traer UNA fuente a pedido, haya ido bien o mal la última vez.

    No es lo mismo que ``retry_step``, y por eso no comparte su puerta: aquel
    repara un paso que quedó fallido, y se niega en cualquier otro caso. Este
    responde a «tráeme lo último de SUNAFIL ahora», que es una petición legítima
    justo cuando el paso anterior terminó **bien** — es entonces cuando el
    usuario quiere saber si hay algo nuevo.

    Sin esto, el botón de sincronizar de cada sección quedaba apagado siempre
    que la última sincronización hubiera funcionado, que es casi siempre.
    """
    source = SOURCES_BY_KEY.get(key)
    if source is None:
        raise CannotRetry(f"La fuente «{key}» no existe.")
    job = _job_para_un_paso(organization)
    if job.step(key) is None:
        # El trabajo es anterior a esta fuente. Añadirle el paso es mejor que
        # negarse: una fuente nueva quedaría inalcanzable para toda empresa que
        # ya hubiera sincronizado, hasta que alguien lanzara una completa.
        job.steps.append({
            "key": source.key,
            "label": source.label,
            "status": StepStatus.PENDING,
            "detail": "",
            "started_at": None,
            "finished_at": None,
        })
        job.save(update_fields=["steps", "updated_at"])
    return _encolar_paso(job, key, requested_by, "el relanzamiento")


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
