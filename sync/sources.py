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
    MANUAL = "manual"      # el «sincronizar ahora» completo, desde el perfil

    # «Tráeme lo nuevo de esta sección, ahora». Es el botón que vive en cada
    # pantalla, y se distingue de MANUAL en una cosa que importa: **no recorre
    # el histórico**. Con MANUAL, los comprobantes caminan hacia atrás mes a
    # mes hasta encontrar tres vacíos seguidos —minutos de espera y decenas de
    # consultas a SUNAT— para traer, casi siempre, lo mismo que ya había. Quien
    # pulsa en su pantalla no está pidiendo la historia: está preguntando si
    # hay algo nuevo desde la última vez.
    NEW = "nuevos"

    ALL = (INITIAL, DAILY, MONTHLY, MANUAL, NEW)


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    needs_sol: bool
    run: Callable[[Credentials, str], dict[str, Any]]
    cadences: frozenset[str]

    def runs_on(self, cadence: str) -> bool:
        # Lo manual siempre corre todo: si alguien pulsa «sincronizar ahora»
        # es porque quiere el cuadro completo. Y lo pedido a mano para UNA
        # sección corre siempre, por definición: se pidió esa.
        return cadence in (Cadence.MANUAL, Cadence.NEW) or cadence in self.cadences


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


def _tributos(creds, cadence: str) -> dict[str, Any]:
    """Régimen de renta desde la Ficha RUC de SOL (tributos afectos)."""
    from accounts.models import Organization, SunatCredential
    from ruc_profile.services.sol_ficha import SolLoginRejected, sync_regime

    organization = Organization.objects.get(ruc=creds.ruc)
    credential = SunatCredential.objects.get(organization=organization)
    try:
        result = sync_regime(organization, credential)
    except SolLoginRejected as exc:
        raise LoginFailed(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise SourceFailed(f"No se pudo leer la Ficha RUC en SOL: {exc}") from exc
    if not result["regimen"]:
        raise SourceFailed("La ficha no muestra un tributo de renta empresarial; el régimen queda sin detectar.")
    return result


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
        ComplianceLoginRejected, CompliancePortalClient, CompliancePortalError,
        ComplianceSynchronizer,
    )

    client = CompliancePortalClient(
        taxpayer_id=creds.ruc, username=creds.username, password=creds.password
    )
    try:
        client.login()
    except ComplianceLoginRejected as exc:
        raise LoginFailed(str(exc)) from exc
    except CompliancePortalError as exc:
        # Todo lo demás —un menú que tarda, una campaña superpuesta, SUNAT
        # caída— no dice nada de la clave. Tratarlo como LoginFailed marcaba la
        # credencial como rechazada y saltaba el buzón, los comprobantes,
        # SUNAFIL y el ITF: un pop-up dejaba la sincronización entera en nada y
        # al usuario mirando un «Credenciales rechazadas» que era mentira.
        raise SourceFailed(str(exc)) from exc
    result = ComplianceSynchronizer(client).run()
    return {"periodos": getattr(result, "stored", 0)}


def _afpnet(creds, cadence: str) -> dict[str, Any]:
    """Aportes previsionales. La única fuente que no puede abrir sesión sola.

    El login de AFPnet lleva un CAPTCHA que resuelve una persona, así que aquí
    se usa la sesión que esa persona dejó abierta. Sin ella el paso falla con un
    motivo accionable en lugar de intentar entrar —que además de inútil, sería
    saltarse un control anti-bots—.

    No recibe las credenciales SOL: no le sirven. Solo usa el RUC para saber de
    qué empresa es la sesión.
    """
    from accounts.models import Organization
    from afpnet.services.client import PortalCerrado
    from afpnet.services.sync import SinSesion, sincronizar

    organization = Organization.objects.filter(ruc=creds.ruc).first()
    if organization is None:
        raise SourceFailed(f"No hay ninguna empresa registrada con RUC {creds.ruc}.")
    try:
        return sincronizar(organization)
    except PortalCerrado as exc:
        # AFPnet cierra por las noches, que es justo cuando corre el reparto
        # programado. Se dice tal cual para que nadie busque una avería.
        raise SourceFailed(str(exc)) from exc
    except SinSesion as exc:
        # No es LoginFailed: aquello invalida la credencial SOL y corta el resto
        # del trabajo. Que AFPnet necesite un CAPTCHA no dice nada de SUNAT.
        raise SourceFailed(str(exc)) from exc


def _sunafil(creds, cadence: str) -> dict[str, Any]:
    from sunafil.services import (
        SunafilClient, SunafilError, SunafilLoginError, SunafilSynchronizer,
    )

    client = SunafilClient(
        taxpayer_id=creds.ruc, username=creds.username, password=creds.password
    )
    try:
        client.login()
    except SunafilLoginError as exc:
        raise LoginFailed(str(exc)) from exc
    except SunafilError as exc:
        # Timeout o portal caído: no dice nada de la clave, y marcarla como
        # rechazada saltaba el resto de fuentes por un portal saturado.
        raise SourceFailed(str(exc)) from exc
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
        # A diario —y cuando alguien pide lo nuevo desde su pantalla— basta
        # recontrastar los meses recientes. Recorrer el histórico entero cada
        # vez costaría minutos y no traería nada que no estuviera ya.
        result = synchronizer.sync_periods(recent_periods(CPE_DAILY_MONTHS))

    # Directo al tablero: los comprobantes traídos se contabilizan y pasan
    # por la cascada de categorización sin esperar otro botón — el mismo
    # contrato que planilla, registros manuales y honorarios.
    from financials.services import categorization as fin_categorization
    from financials.services import ingest as fin_ingest

    fin_ingest.ingest_sunat(creds.ruc)
    fin_categorization.categorize_pending(creds.ruc)
    return {"comprobantes": getattr(result, "created", 0),
            "xml": getattr(result, "xml_downloaded", 0)}


def _rhe(creds, cadence: str) -> dict[str, Any]:
    """Recibos por honorarios recibidos: opción 11.5.1.1.14 del menú SOL
    («Consulta de recibos» del sistema RHE, con la empresa como usuaria),
    capturada de una sesión real.

    Ventanas por cadencia: la inicial camina hacia atrás hasta agotar el
    historial (3 meses vacíos, piso 2017 = RHE obligatorio); la diaria
    recontrasta 2 meses como CPE; la mensual re-barre el ejercicio en
    curso porque la LISTA DE PAGOS del recibo y las reversiones cambian
    después de emitido — un gasto puede dejar de serlo."""
    from sunat_cpe.services.parsing import current_period, recent_periods
    from sunat_rhe.services import RhePortalClient, RheSynchronizer

    client = RhePortalClient(
        taxpayer_id=creds.ruc, username=creds.username, password=creds.password
    )
    synchronizer = RheSynchronizer(client)
    if cadence in (Cadence.INITIAL, Cadence.MANUAL):
        result = synchronizer.backfill(current_period(), stop_after_empty=3)
    elif cadence == Cadence.MONTHLY:
        period = current_period()
        year_to_date = [
            f"{period[:4]}{m:02d}" for m in range(1, int(period[4:]) + 1)
        ]
        result = synchronizer.sync_periods(year_to_date)
    else:
        result = synchronizer.sync_periods(recent_periods(CPE_DAILY_MONTHS))
    # Directo al Estado de Resultados, sin botones intermedios.
    from financials.services import ingest as financials_ingest

    financials_ingest.ingest_fee_receipts(creds.ruc)
    return {"recibos": getattr(result, "created", 0)}


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
    from suppliers.services import SupplierMonitor, incorporar_desde_compras

    detalle: dict[str, Any] = {}
    # Todo emisor con comprobantes entra a la cartera, en cualquier cadencia:
    # es un INSERT, y los que el usuario dejó de vigilar no se rehacen porque
    # su ficha sigue existiendo con ``is_tracked=False``.
    incorporados = incorporar_desde_compras(creds.ruc)
    if incorporados:
        detalle["incorporados"] = incorporados

    cartera = Supplier.objects.tracked().filter(account_ruc=creds.ruc)
    # A pedido («Validar en SUNAT») se consulta todo aunque ya se hubiera
    # mirado hoy: quien pulsa quiere el estado de ahora, no el de esta mañana.
    result = SupplierMonitor().run(
        suppliers=cartera, skip_checked_today=(cadence != Cadence.NEW),
    )
    detalle.update({
        "revisados": result.checked,
        "con_observaciones": result.with_issues,
        "fallidos": result.failed,
    })
    return detalle


def _declaraciones(creds, cadence: str) -> dict[str, Any]:
    """Lo presentado y pagado a SUNAT (Consulta de Declaraciones y Pagos de SOL).

    La primera carga recorre tres años por ventanas de seis meses; las demás
    solo repasan los últimos periodos, que es donde puede haber una
    presentación nueva o una rectificatoria.
    """
    from sunat_declaraciones.services import DeclaracionesLoginRejected, sincronizar

    try:
        result = sincronizar(
            creds.ruc, creds.username, creds.password,
            inicial=cadence in (Cadence.INITIAL, Cadence.MANUAL),
        )
    except DeclaracionesLoginRejected as exc:
        raise LoginFailed(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise SourceFailed(f"No se pudo leer la consulta de declaraciones en SOL: {exc}") from exc
    return {
        "filas": result.filas, "nuevas": result.nuevas,
        "periodos_declarados": result.periodos_declarados,
    }


def _renta_anual(creds, cadence: str) -> dict[str, Any]:
    """La DJ Anual de Renta (F.V. 710) desde e-renta, con sus casillas y el zip."""
    from sunat_declaraciones.services import RentaAnualLoginRejected, sincronizar_renta_anual

    try:
        result = sincronizar_renta_anual(creds.ruc, creds.username, creds.password)
    except RentaAnualLoginRejected as exc:
        raise LoginFailed(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise SourceFailed(f"No se pudo leer la DJ anual en e-renta: {exc}") from exc
    return {"presentaciones": result.presentaciones, "nuevas": result.nuevas}


def _intel(creds, cadence: str) -> dict[str, Any]:
    """Convierte los mensajes nuevos del buzón en resumen, casos y decisiones.

    No sale a internet salvo al modelo: cada mensaje se analiza una sola vez
    (caché por huella), así que la corrida diaria paga solo lo que llegó
    hoy. Sin esto, «Resumen», «Casos» y «Decisiones» quedaban vacíos aunque
    el buzón estuviera al día, porque nada más disparaba el análisis."""
    from sunat_intel.services import analyzer, cases

    stats = analyzer.analyze_pending(taxpayer_id=creds.ruc)
    casos = cases.rebuild_cases(creds.ruc)
    return {**stats, "casos": casos}


def _analytics(creds, cadence: str) -> dict[str, Any]:
    """No sale a internet: lee los XML nuevos y recalcula alertas.

    La extracción iba solo en una tarea global aparte; si esa no corría, el
    detalle de cada comprobante (ítems, IGV, forma de pago) quedaba en
    guiones aunque el XML estuviera guardado."""
    from finance_analytics.services.alerts import rebuild_alerts
    from finance_analytics.services.xml_extract import extract_pending

    extraccion = extract_pending(account_ruc=creds.ruc)
    return {**rebuild_alerts(creds.ruc), "xml": extraccion}


INITIAL = Cadence.INITIAL
DAILY = Cadence.DAILY
MONTHLY = Cadence.MONTHLY

SOURCES: list[Source] = [
    # Datos públicos, sin clave: cambian poco, se revisan una vez al mes.
    Source("ruc_profile", "Ficha RUC", False, _ruc_profile,
           frozenset({INITIAL, MONTHLY})),
    Source("remype", "Acreditación REMYPE", False, _remype,
           frozenset({INITIAL, MONTHLY})),
    # El régimen de renta sale de la Ficha RUC *dentro* de SOL (tributos
    # afectos); cambia muy de tanto en tanto.
    Source("tributos", "Régimen tributario (Ficha RUC)", True, _tributos,
           frozenset({INITIAL, MONTHLY})),
    # Perfil de cumplimiento: SUNAT lo recalcula por trimestre.
    Source("compliance", "Perfil de cumplimiento", True, _compliance,
           frozenset({INITIAL, MONTHLY})),
    # Lo que se mueve a diario y trae plazos que vencen.
    Source("mailbox", "Buzón SUNAT", True, _mailbox,
           frozenset({INITIAL, DAILY})),
    Source("cpe", "Comprobantes electrónicos", True, _cpe,
           frozenset({INITIAL, DAILY})),
    Source("rhe", "Recibos por honorarios", True, _rhe,
           frozenset({INITIAL, DAILY, MONTHLY})),
    Source("sunafil", "Casilla SUNAFIL", True, _sunafil,
           frozenset({INITIAL, DAILY})),
    # AFPnet no usa la clave SOL —de ahí el False— y su sesión la abre una
    # persona resolviendo un CAPTCHA. Se consulta una vez al mes porque los
    # aportes se declaran mensualmente: pedirlo a diario gastaría la sesión sin
    # traer nada nuevo, y cada sesión cuesta un CAPTCHA.
    Source("afpnet", "Aportes AFP (AFPnet)", False, _afpnet,
           frozenset({INITIAL, MONTHLY})),
    # El ITF se publica una vez cerrado el mes.
    Source("itf", "Movimientos bancarios (ITF)", True, _itf,
           frozenset({INITIAL, MONTHLY})),
    # La consulta de RUC es pública: no gasta la sesión SOL ni depende de ella.
    Source("suppliers", "Estado de proveedores", False, _suppliers,
           frozenset({INITIAL, DAILY})),
    # Lo que se presentó y pagó a SUNAT. Las declaraciones son mensuales;
    # va antes de la analítica porque de aquí salen alertas y el declarado
    # que cruza la conciliación.
    Source("declaraciones", "Declaraciones y pagos (SOL)", True, _declaraciones,
           frozenset({INITIAL, MONTHLY})),
    # La DJ anual se presenta una vez al año; una vez al mes basta para
    # recoger rectificatorias y el ejercicio nuevo.
    Source("renta_anual", "Declaración anual de renta (e-renta)", True, _renta_anual,
           frozenset({INITIAL, MONTHLY})),
    # Los dos últimos no salen a los portales: van sobre lo que los pasos
    # anteriores acaban de guardar. El análisis del buzón corre tras el buzón
    # (diario) y en la carga inicial; solo cuesta por mensaje nuevo.
    Source("intel", "Lectura del buzón (IA)", False, _intel,
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
