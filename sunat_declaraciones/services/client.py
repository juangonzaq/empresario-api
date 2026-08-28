"""La «Consulta de Declaraciones y Pagos» de SOL, leída por su propia API.

La pantalla es una aplicación de ``e-plataformaunica.sunat.gob.pe`` que llama
a su API con un JWT en la cabecera ``IdCache``, acuñado por el menú SOL al
abrir cualquier opción de esa plataforma. El JWT vale para toda la API de
recaudación (``/v1/recaudacion/tributaria``), no solo para la opción que lo
acuñó.

Eso importa porque la opción «Consulta de Declaraciones y Pagos» vive en el
menú *nuevo* de SUNAT (``cl-ti-itmenu2``, código 55.2.1.1.1), que pide su
propio login, mientras que el menú clásico —el que ya usan la Ficha RUC y el
perfil de cumplimiento— ofrece otras consultas de la misma plataforma bajo
«Mis Declaraciones y pagos» (grupo 86). Así que se entra con un navegador real
al menú clásico, se abre la primera opción de la plataforma que haya en ese
grupo, se captura la cabecera ``IdCache`` de la primera llamada que hace la
app y, con ella, se consulta directamente el servicio de declaraciones.

La URL de consulta lleva dos rangos —fecha de presentación y periodo
tributario— y dos banderas que dicen cuál de los dos filtra. Se consulta
**solo por periodo** (``false/true``): filtrando por fecha se perdía el 621 de
junio presentado en julio. La pantalla limita cada consulta a seis meses; se
respeta ese tamaño de ventana aunque el servidor acepte más.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

MENU_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm?pestana=*&agrupacion=*"
# El grupo del menú clásico cuyas opciones abren la plataforma de recaudación.
GRUPO_MENU = "86"
# Si el DOM no dejara leer el grupo, estas dos existían el 2026-08-28.
OPCIONES_RESPALDO = ("86.2.1.1.4", "86.2.1.1.5")
APP_MARKER = "e-plataformaunica.sunat.gob.pe"
API_BASE = (
    "https://e-plataformaunica.sunat.gob.pe/v1/recaudacion/tributaria/consultadeclaracion"
    "/t/internet/declaracion/factoriaConsulta"
)
API_BASE_DETALLE = (
    "https://e-plataformaunica.sunat.gob.pe/v1/recaudacion/tributaria/consultadeclaracion"
    "/t/internet/declaracion"
)
REFERER = (
    "https://e-plataformaunica.sunat.gob.pe/app/recaudacion/tributaria/internet/html/"
    "consultaDeclaracionInternetprincipal.html"
)
FORMULARIOS = "0601,0621,1662,detr"
DEFAULT_TIMEOUT_MS = 90_000
POLL_MS = 500


class ConsultaDeclaracionesError(RuntimeError):
    """SOL entró pero la consulta no se pudo hacer o leer."""


class DeclaracionesLoginRejected(ConsultaDeclaracionesError):
    """SOL rechazó usuario o clave."""


def _primer_dia(periodo: str) -> date:
    return date(int(periodo[:4]), int(periodo[4:6]), 1)


def url_consulta(desde: str, hasta: str, formularios: str = FORMULARIOS) -> str:
    """``desde``/``hasta`` son periodos AAAAMM. El rango de fechas se rellena con
    los mismos meses porque la API lo exige aunque no filtre por él."""
    from .plazos import fin_de_mes

    f_ini = _primer_dia(desde).strftime("%Y%m%d")
    f_fin = fin_de_mes(hasta).strftime("%Y%m%d")
    return (
        f"{API_BASE}/{formularios}/{f_ini}/{f_fin}"
        f"/{desde[4:6]}/{desde[:4]}/{hasta[4:6]}/{hasta[:4]}/false/true"
    )


def ventanas(desde: str, hasta: str, meses: int = 6) -> list[tuple[str, str]]:
    """Parte [desde, hasta] en tramos de a lo sumo ``meses`` periodos."""
    from .plazos import periodo_siguiente

    tramos: list[tuple[str, str]] = []
    inicio = desde
    while inicio <= hasta:
        fin = inicio
        for _ in range(meses - 1):
            if fin >= hasta:
                break
            fin = periodo_siguiente(fin)
        fin = min(fin, hasta)
        tramos.append((inicio, fin))
        inicio = periodo_siguiente(fin)
    return tramos


@dataclass
class ConsultaDeclaracionesClient:
    """Entra a SOL, captura el JWT de la app y consulta por ventanas."""

    ruc: str
    username: str
    password: str
    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    _tokens: list[str] = field(default_factory=list, init=False)

    def consultar(
        self, tramos: list[tuple[str, str]], *, detallar: set[str] | None = None,
        omitir: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """(filas, constancias por nro. de orden). ``detallar`` son los
        formularios cuya constancia se pide además (el botón «constancia» de
        la pantalla); ``omitir`` son órdenes que ya se tienen."""
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        from core.browser import browser_env
        from ruc_profile.services.sol_ficha import SolLoginRejected, asegurar_menu_sol
        from sunat_mailbox.services.constants import BROWSER_ARGS, USER_AGENT

        def sniff(request: Any) -> None:
            if APP_MARKER not in request.url:
                return
            token = request.headers.get("idcache", "")
            if token and token not in self._tokens:
                self._tokens.append(token)

        filas: list[dict[str, Any]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless, channel="chromium", args=BROWSER_ARGS, env=browser_env(),
            )
            try:
                ctx = browser.new_context(
                    user_agent=USER_AGENT, locale="es-PE",
                    viewport={"width": 1440, "height": 1000},
                )
                ctx.on("page", lambda p: p.on("request", sniff))
                page = ctx.new_page()
                page.on("dialog", lambda d: d.accept())
                page.on("request", sniff)

                page.goto(MENU_URL, wait_until="networkidle", timeout=self.timeout_ms)
                page.wait_for_selector("#txtRuc", state="visible", timeout=self.timeout_ms)
                page.click("#btnPorRuc")
                page.fill("#txtRuc", self.ruc)
                page.fill("#txtUsuario", self.username)
                page.fill("#txtContrasena", self.password)
                page.click("#btnAceptar")
                try:
                    asegurar_menu_sol(page, self.timeout_ms)
                except SolLoginRejected as exc:
                    raise DeclaracionesLoginRejected(str(exc)) from exc

                self._abrir_plataforma(page)

                cabeceras = {
                    "Accept": "application/json, text/plain, */*",
                    "IdCache": self._tokens[0],
                    "IdFormulario": "*MENU*",
                    "Lang": "es-PE",
                    "Referer": REFERER,
                }
                for desde, hasta in tramos:
                    url = url_consulta(desde, hasta)
                    respuesta = ctx.request.get(url, headers=cabeceras, timeout=60_000)
                    if respuesta.status != 200:
                        raise ConsultaDeclaracionesError(
                            f"SUNAT respondió {respuesta.status} al consultar {desde}-{hasta}."
                        )
                    try:
                        datos = respuesta.json()
                    except Exception as exc:  # noqa: BLE001
                        raise ConsultaDeclaracionesError(
                            f"La consulta {desde}-{hasta} no devolvió JSON."
                        ) from exc
                    if not isinstance(datos, list):
                        raise ConsultaDeclaracionesError(
                            f"La consulta {desde}-{hasta} devolvió algo inesperado: {str(datos)[:120]}"
                        )
                    logger.info("Declaraciones %s %s-%s: %d filas", self.ruc, desde, hasta, len(datos))
                    filas.extend(datos)

                detalles: dict[str, dict[str, Any]] = {}
                pendientes = [
                    str(f.get("numOrd")) for f in filas
                    if detallar and str(f.get("codFor")) in detallar
                    and str(f.get("numOrd")) not in (omitir or set())
                ]
                for nro_orden in dict.fromkeys(pendientes):
                    # El «detalle» del botón son las mismas casillas que ya vienen
                    # en la consulta; la «constancia» es lo que falta: tributos
                    # pagados, forma de pago y, en la PLAME, los trabajadores.
                    constancia = self._json(
                        ctx, f"{API_BASE_DETALLE}/factoriaConstanciaFormulario/{nro_orden}", cabeceras,
                    )
                    if isinstance(constancia, dict):
                        detalles[nro_orden] = constancia
                if pendientes:
                    logger.info("Declaraciones %s: detalle de %d boletas", self.ruc, len(detalles))
            except PlaywrightError as exc:
                raise ConsultaDeclaracionesError(f"El navegador falló en SOL: {exc}") from exc
            finally:
                browser.close()
        return filas, detalles

    @staticmethod
    def _json(ctx: Any, url: str, cabeceras: dict[str, str]) -> Any:
        """Un GET tolerante: el detalle de una boleta que falle no debe tirar
        la sincronización entera; se deja None y se reintenta la próxima."""
        try:
            respuesta = ctx.request.get(url, headers=cabeceras, timeout=30_000)
            if respuesta.status != 200:
                logger.warning("SUNAT respondió %s en %s", respuesta.status, url)
                return None
            return respuesta.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer %s: %s", url, exc)
            return None

    def _opciones_plataforma(self, page: Any) -> list[str]:
        """Los códigos de nivel 4 del grupo «Mis Declaraciones y pagos», con
        las consultas primero: abren la plataforma sin presentar nada."""
        try:
            opciones = page.evaluate(
                """(grupo) => [...document.querySelectorAll(
                    `li.nivel4[data-id^="${grupo}."]`
                )].map(li => ({
                    code: li.getAttribute('data-id'),
                    label: (li.textContent || '').trim(),
                }))""",
                GRUPO_MENU,
            )
        except Exception:  # noqa: BLE001 — se cae al respaldo
            opciones = []
        vistos: list[str] = []
        for o in sorted(opciones, key=lambda o: 0 if "consulta" in o["label"].lower() else 1):
            if o["code"] and o["code"] not in vistos:
                vistos.append(o["code"])
        for code in OPCIONES_RESPALDO:
            if code not in vistos:
                vistos.append(code)
        return vistos

    def _abrir_plataforma(self, page: Any) -> None:
        """Abre opciones de la plataforma hasta que una acuñe el token. La
        llamada es la misma que hace el menú al pulsar una opción
        (``action=execute``); ``iconExecute`` devolvía el login otra vez."""
        espera_ms = min(self.timeout_ms, 40_000)
        for code in self._opciones_plataforma(page)[:4]:
            nivel = code.split(".")[0]
            page.evaluate(
                f"ejecuta('MenuInternet.htm?action=execute&code={code}',false,"
                f"'Consulta','#nivel1_{nivel}','{code}')"
            )
            for _ in range(espera_ms // POLL_MS):
                if self._tokens:
                    logger.info("Token de la plataforma de recaudación capturado vía %s", code)
                    return
                page.wait_for_timeout(POLL_MS)
            logger.info("La opción %s no acuñó token; se prueba la siguiente", code)
        raise ConsultaDeclaracionesError(
            "Ninguna opción de «Mis Declaraciones y pagos» abrió la plataforma de "
            "recaudación en SOL: no se capturó su token. SUNAT pudo mover el menú "
            "o mostrar una campaña."
        )
