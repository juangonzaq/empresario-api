"""Arranque y ejecución del trabajo de sincronización de una empresa."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass

from django.utils import timezone

from accounts.models import Organization, SunatConnectionStatus

from .models import (
    HEARTBEAT_EVERY, STALE_MESSAGE, JobKind, JobStatus, StepStatus, SyncJob,
)
from .sources import (
    Cadence, LoginFailed, SOURCES_BY_KEY, Source, SourceFailed, initial_steps,
    sources_for,
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
    only: list[str] | None = None,
) -> SyncJob:
    """Encola una sincronización. Si ya hay una en marcha, la devuelve en lugar
    de encolar otra: dos scrapeos simultáneos con el mismo usuario SOL se
    estorban entre sí, y SUNAT admite una sesión por usuario.

    ``only`` limita el trabajo a esas fuentes (el checklist del usuario). Si es
    ``None`` corre todas las que toquen a la cadencia."""
    reclaim_stale(organization)
    running = SyncJob.objects.filter(organization=organization).active().first()
    if running is not None:
        return running

    steps = initial_steps(kind)
    if only:
        chosen = set(only)
        subset = [s for s in steps if s["key"] in chosen]
        if subset:
            steps = subset

    job = SyncJob.objects.create(
        organization=organization,
        kind=kind,
        requested_by=requested_by,
        steps=steps,
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


class SyncLimitReached(Exception):
    """Alcanzó el tope diario de sincronizaciones manuales gratis. Se puede
    continuar autorizando el cargo (``accept_charge``); lleva la cuota para
    que la interfaz explique cuánto cuesta."""

    def __init__(self, quota: dict):
        self.quota = quota
        super().__init__("Alcanzaste el tope diario de sincronizaciones manuales.")


def manual_quota(organization: Organization) -> dict:
    """Cuántas sincronizaciones manuales gratis le quedan hoy a la empresa."""
    from django.conf import settings

    from billing.services import extra_manual_sync_price

    limit = organization.manual_sync_daily_limit
    if limit is None:
        limit = int(getattr(settings, "SYNC_MANUAL_DAILY_LIMIT", 2))
    used = SyncJob.objects.filter(
        organization=organization, kind=JobKind.MANUAL,
        created_at__date=timezone.localdate(),
    ).count()
    return {
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "price": str(extra_manual_sync_price()),
        "currency": "PEN",
    }


def start_manual_sync(organization: Organization, requested_by=None, *,
                      only: list[str] | None = None, accept_charge: bool = False):
    """Lanza una sincronización manual respetando el tope diario. Devuelve
    ``(job, charged, quota)``. Si ya hay una en marcha, la devuelve sin contar
    ni cobrar. Pasado el tope y sin ``accept_charge``, lanza ``SyncLimitReached``."""
    running = SyncJob.objects.filter(organization=organization).active().first()
    if running is not None:
        return running, False, manual_quota(organization)

    quota = manual_quota(organization)
    charged = False
    if quota["remaining"] <= 0:
        if not accept_charge:
            raise SyncLimitReached(quota)
        job = start_sync(organization, kind=JobKind.MANUAL, requested_by=requested_by, only=only)
        # Solo se cobra si de verdad se creó un trabajo nuevo (no un dedupe).
        if job.kind == JobKind.MANUAL:
            from billing.services import charge_extra_manual_sync

            charge_extra_manual_sync(organization, reference=str(job.id), user=requested_by)
            charged = True
        return job, charged, manual_quota(organization)

    job = start_sync(organization, kind=JobKind.MANUAL, requested_by=requested_by, only=only)
    return job, charged, manual_quota(organization)


def _sol_ya_funciono(job: SyncJob) -> bool:
    """¿Ya entró alguna fuente a SUNAT con esta misma clave, en este trabajo?

    Es la prueba de que la clave sirve, y es mejor prueba que la sospecha de
    quien falla después. Los clientes de los portales **no saben** distinguir
    «me rechazaron la clave» de «no llegué»: el de SUNAFIL lo dice en su propio
    mensaje —«revisa las credenciales, *o* si SUNAT está mostrando un
    captcha»—, y esa duda se estaba resolviendo siempre en contra del usuario.

    Visto en producción: buzón trajo 225 mensajes y comprobantes 1.254 con esa
    clave; SUNAFIL falló a continuación, la credencial quedó marcada como
    rechazada y el ITF se saltó entero. Nada de eso era cierto.
    """
    for paso in job.steps:
        fuente = SOURCES_BY_KEY.get(paso.get("key", ""))
        if fuente and fuente.needs_sol and paso.get("status") == StepStatus.DONE:
            return True
    return False


def _motivo_amigable(exc: Exception) -> str:
    """Lo que ve la persona en el paso caído.

    El traceback va al log; aquí va una frase que diga qué pasó y qué hacer.
    Un «Read timed out (read timeout=60)» de urllib3 en pantalla no explica
    nada y parece un fallo nuestro, cuando es SUNAT que no contestó.
    """
    from requests import exceptions as rq

    if isinstance(exc, SourceFailed):
        # Viene redactada por la fuente, pero a veces arrastra el error crudo
        # del navegador detrás de los dos puntos.
        return _sin_ruido_tecnico(str(exc))
    # Playwright (ITF, perfil de cumplimiento) tiene su propio TimeoutError;
    # se detecta por nombre para no importar la librería aquí.
    if isinstance(exc, (rq.Timeout, TimeoutError)) \
            or type(exc).__name__ == "TimeoutError":
        return ("SUNAT tardó demasiado en responder y se cortó la consulta. "
                "Suele ser temporal: vuelve a intentarlo en unos minutos.")
    if isinstance(exc, rq.SSLError):
        return ("No se pudo establecer una conexión segura con SUNAT. "
                "Vuelve a intentarlo en unos minutos.")
    if isinstance(exc, (rq.ConnectionError, ConnectionError, OSError)):
        return ("No se pudo conectar con SUNAT. Revisa tu conexión o vuelve a "
                "intentarlo en unos minutos.")
    if isinstance(exc, rq.HTTPError):
        codigo = getattr(getattr(exc, "response", None), "status_code", None)
        if codigo and codigo >= 500:
            return ("SUNAT respondió con un error en su servidor. "
                    "Vuelve a intentarlo más tarde.")
        return ("SUNAT rechazó la consulta. Si se repite, desconecta y vuelve "
                "a conectar tus credenciales.")
    return ("No se pudo completar esta fuente por un error inesperado. "
            "Vuelve a intentarlo; si persiste, avísanos.")


# Huellas de un error de Playwright/JS que no le dicen nada a nadie.
_RUIDO_TECNICO = (
    "Page.", "Frame.", "Locator.", "wait_for_", "Timeout ", "ms exceeded",
    "ReferenceError", "TypeError", "at eval", "UtilityScript", "--headful",
    "Traceback", "playwright",
)


def _sin_ruido_tecnico(texto: str) -> str:
    """Deja la frase humana y sustituye la parte técnica por qué hacer.

    «No se pudo leer la Ficha RUC en SOL: Page.evaluate: ReferenceError…»
    pasa a «No se pudo leer la Ficha RUC en SOL. SUNAT no respondió como se
    esperaba; suele ser temporal, vuelve a intentarlo en unos minutos.» El
    detalle crudo ya está en el log, que es donde sirve.
    """
    if not any(huella in texto for huella in _RUIDO_TECNICO):
        return texto
    cabeza, _, _ = texto.partition(":")
    cabeza = cabeza.strip().rstrip(".")
    if any(huella in cabeza for huella in _RUIDO_TECNICO) or len(cabeza) < 12:
        cabeza = "No se pudo completar esta fuente"
    if "exceeded" in texto or "Timeout" in texto:
        cola = "SUNAT tardó demasiado en responder. Suele ser temporal: vuelve a intentarlo en unos minutos."
    else:
        cola = "SUNAT no respondió como se esperaba. Suele ser temporal: vuelve a intentarlo en unos minutos."
    return f"{cabeza}. {cola}"


def _run_source(
    job: SyncJob,
    source: Source,
    credentials: Credentials,
    cadence: str | None = None,
) -> str:
    """Corre una fuente y deja su resultado escrito en el paso del trabajo.

    Devuelve el motivo por el que no se pudo entrar a SUNAT, o cadena vacía.
    Ese motivo es fatal para el resto del trabajo —no tiene sentido insistir
    con una clave que el portal acaba de rechazar— pero quien decide qué hacer
    con él es el llamador, porque en un reintento de un paso suelto no hay
    resto que cortar.
    """
    organization = job.organization

    job.mark_step(source.key, StepStatus.RUNNING)
    try:
        # Sin cadencia explícita manda la del trabajo. La explícita existe
        # porque el botón de una sección pide «lo nuevo» sobre un trabajo que
        # pudo ser el inicial: con `job.kind` heredaría su recorrido completo
        # del histórico, que es justo lo que ese botón no debe hacer.
        result = source.run(credentials, cadence or job.kind)
    except LoginFailed as exc:
        # Si otra fuente ya entró con esta clave, el fallo es del portal y no
        # de la clave: se anota el paso y el trabajo sigue. Invalidar aquí le
        # costaba al usuario los pasos que faltaban y un aviso de credenciales
        # rechazadas que era mentira.
        if _sol_ya_funciono(job):
            logger.warning(
                "%s dice que el login falló, pero otra fuente ya entró con esa "
                "clave en este trabajo: se trata como fallo del portal (%s)",
                source.key, organization.ruc,
            )
            job.mark_step(source.key, StepStatus.FAILED, str(exc)[:300])
            return ""

        # Credenciales malas: el usuario debe volver a conectarse. Se escribe
        # con un UPDATE sobre la fila que exista *ahora*, no sobre el objeto
        # que se cargó al arrancar: el usuario puede haber desconectado o
        # cambiado la clave mientras el paso corría, y guardar una fila que
        # ya no está tumbaba el trabajo entero con `NotUpdated`.
        _actualizar_credencial(
            organization,
            status=SunatConnectionStatus.INVALID,
            last_error=str(exc)[:500],
        )
        job.mark_step(source.key, StepStatus.FAILED, "Credenciales rechazadas")
        logger.warning("Login SUNAT rechazado para %s", organization.ruc)
        return str(exc)
    except Exception as exc:  # noqa: BLE001 — un paso caído no tumba el resto
        logger.exception("Falló el paso %s de %s", source.key, organization.ruc)
        job.mark_step(source.key, StepStatus.FAILED, _motivo_amigable(exc)[:300])
        return ""

    detail = ", ".join(f"{k}: {v}" for k, v in (result or {}).items())
    job.mark_step(source.key, StepStatus.DONE, detail)
    # El primer portal que responde confirma que la clave sirve.
    if source.needs_sol:
        _actualizar_credencial(
            organization,
            status=SunatConnectionStatus.CONNECTED,
            last_verified_at=timezone.now(),
            last_error="",
            solo_si_no=SunatConnectionStatus.CONNECTED,
        )
    return ""


def _actualizar_credencial(organization, *, solo_si_no: str = "", **campos) -> None:
    """Escribe sobre la credencial SOL vigente de la empresa, si la hay.

    UPDATE por consulta y no ``save()``: la credencial puede haber sido borrada
    o sustituida por el usuario mientras el trabajo corría, y en ese caso no
    hay nada que anotar —la nueva ya trae su propio estado «pendiente»—.
    """
    from accounts.models import SunatCredential

    filas = SunatCredential.objects.filter(organization=organization)
    if solo_si_no:
        filas = filas.exclude(status=solo_si_no)
    filas.update(**campos, updated_at=timezone.now())


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


@contextmanager
def _latido(job: SyncJob):
    """Mantiene fresco `updated_at` mientras el trabajo corre.

    Un paso (comprobantes, ITF) puede pasar muchos minutos dentro de una sola
    llamada sin tocar la base; sin latido no hay forma de distinguir «sigue
    trabajando» de «el worker murió». Con él, `STALE_AFTER` puede ser corto y
    un trabajo huérfano se cierra en minutos, no en horas.

    Hilo demonio con su propia conexión (se cierra al salir). Solo escribe si
    el trabajo sigue en marcha, para no resucitar uno cancelado o terminado.
    """
    from django.db import connection

    stop = threading.Event()

    def latir() -> None:
        try:
            while not stop.wait(HEARTBEAT_EVERY.total_seconds()):
                SyncJob.objects.filter(
                    pk=job.pk, status=JobStatus.RUNNING,
                ).update(updated_at=timezone.now())
        finally:
            connection.close()

    hilo = threading.Thread(target=latir, name=f"sync-latido-{job.pk}", daemon=True)
    hilo.start()
    try:
        yield
    finally:
        stop.set()
        hilo.join(timeout=5)


def execute(job: SyncJob) -> SyncJob:
    """Corre los pasos en serie. Aislado de Celery para poder probarlo.

    Corre las fuentes que el trabajo tiene en ``steps`` —que normalmente son las
    de su cadencia, pero pueden ser un subconjunto elegido por el usuario—."""
    with _latido(job):
        try:
            return _execute(job)
        except Exception as exc:  # noqa: BLE001 — el trabajo se cierra sí o sí
            return _cerrar_por_error(job, exc)


def _execute(job: SyncJob) -> SyncJob:
    organization = job.organization
    sources = [
        SOURCES_BY_KEY[s["key"]] for s in job.steps if s["key"] in SOURCES_BY_KEY
    ] or sources_for(job.kind)
    # Cancelado mientras esperaba en la cola: no se arranca nada.
    if job.cancellation_requested():
        return _abandonar_por_cancelacion(job)
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
        # La cancelación es cooperativa: se mira entre paso y paso. El paso en
        # curso termina lo suyo (un scrapeo a medias no se corta limpio), pero
        # no arranca ninguno más.
        if job.cancellation_requested():
            return _abandonar_por_cancelacion(job)
        if fatal and source.needs_sol:
            job.mark_step(source.key, StepStatus.SKIPPED, "No se pudo entrar a SUNAT")
            continue
        fatal = _run_source(job, source, credentials) or fatal

    if job.cancellation_requested():
        return _abandonar_por_cancelacion(job)
    return _wrap_up(job, fatal)


def _abandonar_por_cancelacion(job: SyncJob) -> SyncJob:
    """Cierra la corrida respetando la cancelación.

    Se re-marca desde el estado fresco de la base: el ``mark_step`` del paso
    que estaba corriendo escribe la lista de pasos que este proceso tiene en
    memoria, y eso puede haber pisado los «omitido» que dejó ``cancel()``."""
    job.refresh_from_db()
    job.cancel()
    logger.info(
        "Sincronización de %s cancelada por el usuario", job.organization.ruc
    )
    return job


def execute_step(job: SyncJob, key: str, cadence: str | None = None) -> SyncJob:
    """Vuelve a correr UN paso del trabajo y recalcula su estado.

    Los demás pasos conservan lo que ya habían traído, así que reintentar el
    buzón después de arreglar la clave no obliga a repetir el histórico de
    comprobantes, que es lo que tarda de verdad.
    """
    source = SOURCES_BY_KEY.get(key)
    if source is None:
        raise CannotRetry(f"El paso «{key}» no existe.")

    with _latido(job):
        try:
            return _execute_step(job, source, key, cadence)
        except Exception as exc:  # noqa: BLE001
            return _cerrar_por_error(job, exc)


def _cerrar_por_error(job: SyncJob, exc: Exception) -> SyncJob:
    """Un error fuera de los pasos no debe dejar el trabajo «ejecutando».

    Cada paso ya atrapa lo suyo; esto recoge lo que se cuela entre pasos (una
    fila borrada, la base caída un instante). Sin esto el trabajo quedaba
    girando hasta que el plazo de abandono lo cerraba, y mientras tanto el
    usuario no podía relanzar nada.
    """
    logger.exception("La sincronización de %s cayó fuera de un paso", job.organization.ruc)
    job.refresh_from_db()
    for step in job.steps:
        if step.get("status") == StepStatus.RUNNING:
            step["status"] = StepStatus.FAILED
            step["detail"] = "Se interrumpió por un error interno"
            step["finished_at"] = timezone.now().isoformat()
        elif step.get("status") == StepStatus.PENDING:
            step["status"] = StepStatus.SKIPPED
            step["detail"] = "No llegó a correr"
    job.save(update_fields=["steps", "updated_at"])
    job.finish(error=f"La sincronización se interrumpió: {str(exc)[:200]}")
    return job


def _execute_step(job: SyncJob, source: Source, key: str, cadence: str | None) -> SyncJob:
    job.start()
    try:
        credentials = credentials_for(job.organization)
    except NotConnected as exc:
        if source.needs_sol:
            job.mark_step(key, StepStatus.SKIPPED, "Sin credenciales")
            job.finish(error=str(exc))
            return job
        # Media docena de fuentes no pasan por SOL: consultar el RUC de un
        # proveedor es público, y AFPnet tiene su propia sesión. Negarles la
        # corrida por una clave que no usan dejaba el botón «revisar ahora» de
        # Proveedores contestando «sin credenciales» a una empresa que solo
        # quería mirar a sus proveedores.
        credentials = Credentials(
            ruc=job.organization.ruc, username="", password=""
        )

    fatal = _run_source(job, source, credentials, cadence)
    return _wrap_up(job, fatal)


def cancel_sync(organization: Organization) -> SyncJob:
    """Cancela el trabajo en curso (o en cola) de la empresa."""
    job = (
        SyncJob.objects.filter(organization=organization)
        .unfinished().order_by("-created_at").first()
    )
    if job is None:
        raise CannotRetry("No hay ninguna sincronización en curso que cancelar.")
    job.cancel()
    return job


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


def _encolar_paso(
    job: SyncJob, key: str, requested_by, accion: str, cadence: str | None = None,
) -> SyncJob:
    if requested_by is not None:
        job.requested_by = requested_by
        job.save(update_fields=["requested_by", "updated_at"])
    job.reopen_step(key)

    from .tasks import run_sync_step

    try:
        run_sync_step.apply_async((str(job.id), key, cadence))
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

    Corre con cadencia ``NEW``: trae lo nuevo y **no recorre el histórico**.
    Antes heredaba la del trabajo, y como el trabajo de una empresa recién
    conectada es el inicial, pulsar «traer nuevos» en Finanzas lanzaba el
    recorrido completo de comprobantes hacia atrás —minutos de espera para
    volver a guardar lo que ya estaba—. Quien quiera el histórico tiene la
    sincronización completa, que es donde eso se pide de verdad.
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
    return _encolar_paso(
        job, key, requested_by, "el relanzamiento", cadence=Cadence.NEW
    )


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
