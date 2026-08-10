"""Las fuentes que se sincronizan para una empresa, y en qué orden.

Cada adaptador recibe las credenciales de *esa* empresa y llama al mismo
cliente y sincronizador que ya usaban las tareas programadas. Los scrapers
siempre aceptaron ``(taxpayer_id, username, password)`` como parámetros; lo
único que era de una sola empresa eran los llamadores.

El orden importa: primero lo que no necesita clave (así la ficha del RUC
aparece en pantalla en segundos), después lo que exige entrar al portal, y al
final la analítica, que solo recalcula sobre lo ya guardado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class LoginFailed(Exception):
    """Las credenciales SOL no sirven. Corta el resto del trabajo."""


class SourceFailed(Exception):
    """La fuente no trajo nada, pero el resto del trabajo puede seguir."""


class Credentials(Protocol):
    ruc: str
    username: str
    password: str


class Cadence:
    """Cada cuánto tiene sentido volver a preguntarle a la fuente.

    No todo cambia al mismo ritmo: los comprobantes y el buzón se mueven a
    diario, mientras que la ficha RUC, el REMYPE o el perfil de cumplimiento
    apenas cambian de mes a mes. Traerlo todo cada día sería castigar a SUNAT
    —y a nuestras credenciales— sin ganar información.
    """

    INITIAL = "inicial"    # primera carga, al conectar
    DAILY = "diaria"
    MONTHLY = "mensual"
    MANUAL = "manual"      # relanzada a mano desde la interfaz

    ALL = (INITIAL, DAILY, MONTHLY, MANUAL)


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    needs_sol: bool
    run: Callable[[Credentials, str], dict[str, Any]]
    cadences: frozenset[str]

    def runs_on(self, cadence: str) -> bool:
        # Lo manual siempre corre todo: si alguien pulsa «sincronizar ahora»
        # es porque quiere el cuadro completo.
        return cadence == Cadence.MANUAL or cadence in self.cadences


# ── Adaptadores ──

def _fallo(result, motivo: Callable[[], str], generico: str) -> None:
    """Convierte en excepción el fallo que el sincronizador solo contó.

    Los sincronizadores recorren una lista de RUC: uno que falla se anota en
    ``failed`` y el recorrido sigue, que es lo correcto cuando se procesan
    muchas empresas de un tirón. Aquí siempre se les pasa un solo RUC, así que
    ese mismo comportamiento pintaba de verde un paso que no había traído
    nada: en pantalla se leía «Listo — 0 consultados» mientras el log
    guardaba la caída del navegador. El motivo ya quedó grabado al fallar; se
    busca —solo si hubo fallo— para que la pantalla diga qué pasó y no
    únicamente que pasó algo.
    """
    if getattr(result, "failed", 0):
        raise SourceFailed(motivo() or generico)


def _ruc_profile(creds, cadence: str) -> dict[str, Any]:
    from ruc_profile.models import RucSnapshot
    from ruc_profile.services import RucProfileSynchronizer

    result = RucProfileSynchronizer().run([creds.ruc], max_age_days=0)
    _fallo(
        result,
        lambda: getattr(
            RucSnapshot.objects.filter(ruc=creds.ruc, succeeded=False)
            .order_by("-captured_on").first(),
            "error", "",
        ),
        "No se pudo capturar la ficha RUC.",
    )
    return {"capturados": getattr(result, "captured", 0)}


def _remype(creds, cadence: str) -> dict[str, Any]:
    from remype.models import RemypeCheck
    from remype.services import RemypeSynchronizer

    result = RemypeSynchronizer().run([creds.ruc], max_age_days=0)
    _fallo(
        result,
        lambda: getattr(
            RemypeCheck.objects.filter(ruc=creds.ruc, succeeded=False)
            .order_by("-checked_on").first(),
            "message", "",
        ),
        "No se pudo consultar la acreditación REMYPE.",
    )
    return {"consultados": getattr(result, "checked", 0)}


def _compliance(creds, cadence: str) -> dict[str, Any]:
    from compliance_profile.services import (
        CompliancePortalClient, CompliancePortalError, ComplianceSynchronizer,
    )

    client = CompliancePortalClient(
        taxpayer_id=creds.ruc, username=creds.username, password=creds.password
    )
    try:
        client.login()
    except CompliancePortalError as exc:
        raise LoginFailed(str(exc)) from exc
    result = ComplianceSynchronizer(client).run()
    return {"periodos": getattr(result, "stored", 0)}


def _sunafil(creds, cadence: str) -> dict[str, Any]:
    from sunafil.services import SunafilClient, SunafilLoginError, SunafilSynchronizer

    client = SunafilClient(
        taxpayer_id=creds.ruc, username=creds.username, password=creds.password
    )
    try:
        client.login()
    except SunafilLoginError as exc:
        raise LoginFailed(str(exc)) from exc
    result = SunafilSynchronizer(client).run()
    return {"creados": getattr(result, "created", 0),
            "actualizados": getattr(result, "updated", 0)}


def _mailbox(creds, cadence: str) -> dict[str, Any]:
    from sunat_mailbox.services import (
        MailboxSynchronizer, SunatLoginError, SunatMailboxClient,
    )

    client = SunatMailboxClient(
        taxpayer_id=creds.ruc, username=creds.username, password=creds.password
    )
    try:
        client.login()
    except SunatLoginError as exc:
        raise LoginFailed(str(exc)) from exc
    result = MailboxSynchronizer(client).run()
    return {"mensajes": getattr(result, "created", 0)}


# Meses que se recontrastan en la corrida diaria. Una nota de crédito puede
# emitirse semanas después de la factura que corrige, así que no basta con
# mirar el mes en curso.
CPE_DAILY_MONTHS = 2


def _cpe(creds, cadence: str) -> dict[str, Any]:
    from sunat_cpe.services import CpePortalClient, CpePortalError, CpeSynchronizer
    from sunat_cpe.services.parsing import current_period, recent_periods

    client = CpePortalClient(
        taxpayer_id=creds.ruc, username=creds.username, password=creds.password
    )
    try:
        client.login()
    except CpePortalError as exc:
        raise LoginFailed(str(exc)) from exc

    synchronizer = CpeSynchronizer(client)
    if cadence in (Cadence.INITIAL, Cadence.MANUAL):
        # Primera carga: se camina hacia atrás hasta que se acaban los datos.
        result = synchronizer.backfill(current_period(), stop_after_empty=3)
    else:
        # A diario basta recontrastar los meses recientes; recorrer el
        # histórico entero cada noche costaría horas y no traería nada nuevo.
        result = synchronizer.sync_periods(recent_periods(CPE_DAILY_MONTHS))
    return {"comprobantes": getattr(result, "created", 0),
            "xml": getattr(result, "xml_downloaded", 0)}


def _itf(creds, cadence: str) -> dict[str, Any]:
    from sunat_itf.services import ItfPortalClient, ItfPortalError, ItfSynchronizer
    from sunat_itf.services.parsing import previous_period

    client = ItfPortalClient(
        taxpayer_id=creds.ruc, username=creds.username, password=creds.password
    )
    try:
        client.login()
    except ItfPortalError as exc:
        raise LoginFailed(str(exc)) from exc
    result = ItfSynchronizer(client).run(previous_period())
    return {"movimientos": getattr(result, "stored", 0)}


def _suppliers(creds, cadence: str) -> dict[str, Any]:
    """Revisa en SUNAT el estado de los proveedores de ESTA empresa.

    No necesita clave SOL: la consulta de RUC es pública. Estaba solo en el
    trabajo de las 07:00, así que una empresa recién registrada veía toda su
    cartera como «nunca consultado» hasta la mañana siguiente — justo cuando
    más falta hace mirarla.

    En la primera carga la cartera se puebla sola con los proveedores a los que
    ya se les compra, sacados de los comprobantes que el paso anterior acaba de
    traer. Sin eso este paso no haría nada en la sincronización que más
    importa: la de quien acaba de registrarse y todavía no ha dado de alta a
    nadie. Después ya no se incorpora nadie por su cuenta —para no deshacer las
    bajas que haya hecho el usuario—, pero se informa de cuántos hay
    pendientes.
    """
    from suppliers.models import Supplier
    from suppliers.services import (
        SupplierMonitor, incorporar_desde_compras, proveedores_por_descubrir,
    )

    detalle: dict[str, Any] = {}
    if cadence == Cadence.INITIAL:
        detalle["incorporados"] = incorporar_desde_compras(creds.ruc)

    cartera = Supplier.objects.tracked().filter(account_ruc=creds.ruc)
    result = SupplierMonitor().run(suppliers=cartera, skip_checked_today=True)
    detalle.update({
        "revisados": result.checked,
        "con_observaciones": result.with_issues,
        "fallidos": result.failed,
    })

    if cadence != Cadence.INITIAL:
        pendientes = len(proveedores_por_descubrir(creds.ruc))
        if pendientes:
            detalle["por_incorporar"] = pendientes
    return detalle


def _intel(creds, cadence: str) -> dict[str, Any]:
    """Pasa por el modelo los mensajes nuevos y reagrupa los casos.

    Es lo que alimenta al asistente y al centro de casos. No sale a los
    portales, pero sí cuesta dinero: cada mensaje sin analizar es una llamada
    al modelo. El análisis se cachea por huella, así que una sincronización
    sin mensajes nuevos no gasta nada, y va acotado a ESTA empresa para no
    pagar por los mensajes de las demás.
    """
    from sunat_intel.services import analyzer, cases

    stats = analyzer.analyze_pending(taxpayer_id=creds.ruc)
    if stats["analyzed"] == 0 and stats["failed"]:
        # Sin clave de OpenAI o con el modelo caído fallan todos. Callarlo
        # dejaría al asistente respondiendo sobre datos viejos sin avisar.
        raise SourceFailed(
            f"No se pudo analizar ninguno de los {stats['failed']} mensajes "
            f"pendientes."
        )
    case_stats = cases.rebuild_cases(creds.ruc)
    return {
        "analizados": stats["analyzed"],
        "fallidos": stats["failed"],
        "casos": case_stats["created"] + case_stats["updated"],
    }


def _analytics(creds, cadence: str) -> dict[str, Any]:
    """No sale a internet: recalcula alertas sobre lo ya guardado."""
    from finance_analytics.services.alerts import rebuild_alerts

    return rebuild_alerts(creds.ruc)


INITIAL = Cadence.INITIAL
DAILY = Cadence.DAILY
MONTHLY = Cadence.MONTHLY

SOURCES: list[Source] = [
    # Datos públicos, sin clave: cambian poco, se revisan una vez al mes.
    Source("ruc_profile", "Ficha RUC", False, _ruc_profile,
           frozenset({INITIAL, MONTHLY})),
    Source("remype", "Acreditación REMYPE", False, _remype,
           frozenset({INITIAL, MONTHLY})),
    # Perfil de cumplimiento: SUNAT lo recalcula por trimestre.
    Source("compliance", "Perfil de cumplimiento", True, _compliance,
           frozenset({INITIAL, MONTHLY})),
    # Lo que se mueve a diario y trae plazos que vencen.
    Source("mailbox", "Buzón SUNAT", True, _mailbox,
           frozenset({INITIAL, DAILY})),
    Source("cpe", "Comprobantes electrónicos", True, _cpe,
           frozenset({INITIAL, DAILY})),
    Source("sunafil", "Casilla SUNAFIL", True, _sunafil,
           frozenset({INITIAL, DAILY})),
    # El ITF se publica una vez cerrado el mes.
    Source("itf", "Movimientos bancarios (ITF)", True, _itf,
           frozenset({INITIAL, MONTHLY})),
    # La consulta de RUC es pública: no gasta la sesión SOL ni depende de ella.
    Source("suppliers", "Estado de proveedores", False, _suppliers,
           frozenset({INITIAL, DAILY})),
    # Ni el análisis ni la analítica salen a los portales: van al final, sobre
    # lo que los pasos anteriores acaban de guardar. El de IA sigue al buzón
    # porque es lo que analiza, pero se deja aquí para que los scrapeos —que
    # son los que pueden perder la sesión de SUNAT— terminen cuanto antes.
    Source("intel", "Análisis con IA", False, _intel,
           frozenset({INITIAL, DAILY})),
    Source("analytics", "Analítica financiera", False, _analytics,
           frozenset({INITIAL, DAILY, MONTHLY})),
]

SOURCES_BY_KEY = {source.key: source for source in SOURCES}


def sources_for(cadence: str) -> list[Source]:
    return [source for source in SOURCES if source.runs_on(cadence)]


def initial_steps(cadence: str = Cadence.INITIAL) -> list[dict[str, Any]]:
    """Los pasos que verá el usuario, solo los que corren en esta cadencia."""
    return [
        {
            "key": source.key,
            "label": source.label,
            "status": "pendiente",
            "detail": "",
            "started_at": None,
            "finished_at": None,
        }
        for source in sources_for(cadence)
    ]
