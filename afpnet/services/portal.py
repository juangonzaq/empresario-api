"""Navegación autenticada por AFPnet, sin copiar nada de un navegador.

Cada consulta del portal es un POST que exige un ``__RequestVerificationToken``
—antifalsificación de ASP.NET, atado a la cookie de la sesión— y, en el caso de
las planillas, un ``RucEmpresaEncriptado`` que solo publica el propio portal.
Ambos se obtienen pidiendo antes la página con GET, que es exactamente lo que
hace un navegador.

Tres cosas que se comprobaron contra el portal real y que ahorran trabajo:

* ``?parguid=`` **no hace falta**. Aparece en la barra de direcciones al navegar
  por el menú, pero la página responde igual sin él.
* ``IdTabSession`` puede ir vacío.
* El rango de devengues admite un año entero de una vez, así que no hay que
  pedir mes a mes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import requests

from . import client, parsers

logger = logging.getLogger(__name__)

# Páginas del área privada. Se piden con GET para sacar de ellas los tokens.
RUTA_EMPRESA = "/GestionarEmpleador/Empleador/ModificarDatosEmpresa"
RUTA_PLANILLAS = "/GestionarPlanilla/Planilla/Listar"
RUTA_RESUMEN_OP = "/GestionarObligacionPago/Devengue/ListarResumenSituacionOpPorDevengue"
RUTA_PAGINA_RESUMEN = "/GestionarObligacionPago/Afiliado/Listar"
RUTA_DEUDAS = "/GestionarDeuda/Reporte/DeudasCiertasYPresuntas"
RUTA_AFILIADO = "/GestionarAfiliado/Afiliado/ConsultarAfiliado"
RUTA_HISTORIAL = "/GestionarObligacionPago/Afiliado/getSessionOpListJson"

# Las seis administradoras que el portal admite en `CodigoAFP`. Se recorren
# todas porque una empresa puede tener trabajadores repartidos entre varias.
CODIGOS_AFP = tuple(parsers.AFP_POR_CODIGO)

# `TipoBusqueda=3` es «por rango de devengue», que es lo que interesa para el
# histórico; `TipoDocumentoPago=PLA`, planillas.
TIPO_BUSQUEDA_DEVENGUE = "3"
TIPO_DOCUMENTO_PLANILLA = "PLA"
TIPO_USUARIO_EMPLEADOR = "2"


class PortalError(client.AfpnetError):
    """Una consulta del portal no salió como se esperaba."""


@dataclass
class Portal:
    """Una sesión abierta contra AFPnet, lista para consultar.

    Los tokens se cachean por página: una sincronización hace varias consultas
    seguidas y no tiene sentido volver a pedir la misma página para cada una.
    """

    cookies: dict[str, str]
    _sesion: requests.Session = field(init=False, repr=False)
    _tokens: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _ruc_encriptado: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        self._sesion = client.sesion_autenticada(self.cookies)

    # ── Plomería ──────────────────────────────────────────────────────────
    def _get(self, ruta: str) -> str:
        try:
            respuesta = self._sesion.get(f"{client.BASE}{ruta}", timeout=client.TIMEOUT)
        except requests.RequestException as exc:
            raise PortalError(f"No pudimos abrir {ruta}: {exc}") from exc

        if respuesta.status_code == 408 or client.RUTA_TIEMPO_AGOTADO in respuesta.url:
            raise client.SesionCaducada(
                "La sesión de AFPnet caducó por inactividad. Vuelve a iniciarla."
            )
        client.comprobar_abierto(respuesta.text)
        if client.MARCA_LOGIN in respuesta.text:
            raise client.SesionCaducada(
                "La sesión de AFPnet caducó. Hay que volver a iniciarla."
            )
        return respuesta.text

    def _token(self, ruta: str) -> str:
        """El token antifalsificación de una página, pidiéndola si hace falta."""
        if ruta not in self._tokens:
            html = self._get(ruta)
            encontrado = client.RE_TOKEN.search(html)
            if not encontrado:
                raise PortalError(
                    f"La página {ruta} no trae token antifalsificación; puede "
                    "que AFPnet la haya cambiado."
                )
            self._tokens[ruta] = encontrado.group(1)
            if ruta == RUTA_PLANILLAS:
                self._ruc_encriptado = _buscar_ruc_encriptado(html)
        return self._tokens[ruta]

    @property
    def ruc_encriptado(self) -> str:
        """Identificador de la empresa que exige la consulta de planillas.

        Lo genera el portal; no se puede derivar del RUC, así que se lee de la
        página de planillas la primera vez que hace falta.
        """
        if not self._ruc_encriptado:
            self._token(RUTA_PLANILLAS)
        return self._ruc_encriptado

    def _post(self, ruta: str, datos: dict[str, str], ruta_token: str = "") -> str:
        campos = dict(datos)
        campos["__RequestVerificationToken"] = self._token(ruta_token or ruta)
        campos["X-Requested-With"] = "XMLHttpRequest"
        try:
            respuesta = self._sesion.post(
                f"{client.BASE}{ruta}",
                data=campos,
                timeout=client.TIMEOUT,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": client.BASE,
                    "Content-Type":
                        "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            respuesta.raise_for_status()
        except requests.RequestException as exc:
            raise PortalError(f"Falló la consulta {ruta}: {exc}") from exc
        client.comprobar_abierto(respuesta.text)
        return respuesta.text

    # ── Consultas ─────────────────────────────────────────────────────────
    def datos_empresa(self) -> parsers.DatosEmpresa:
        """Los datos que AFPnet tiene de la empresa.

        Se leen del formulario de modificación porque es la única pantalla que
        los publica. **Solo se lee**: un POST ahí cambiaría los datos de la
        empresa en el sistema previsional.
        """
        return parsers.parsear_datos_empresa(self._get(RUTA_EMPRESA))

    def planillas(
        self, desde: str, hasta: str, afps: tuple[str, ...] = CODIGOS_AFP
    ) -> list[parsers.Planilla]:
        """Las planillas de un rango de devengues, recorriendo cada AFP.

        El portal obliga a elegir una administradora por consulta, así que se
        pregunta por todas: una empresa puede tener trabajadores repartidos, y
        consultar solo una escondería las planillas del resto.
        """
        encontradas: list[parsers.Planilla] = []
        for codigo in afps:
            html = self._post(RUTA_PLANILLAS, {
                "FlagSegundaFirma": "",
                "CodigoTipoUsuario": TIPO_USUARIO_EMPLEADOR,
                "RucEmpresaEncriptado": self.ruc_encriptado,
                "TipoDocumentoPago": TIPO_DOCUMENTO_PLANILLA,
                "EsEstadistica": "false",
                "IdTabSession": "",
                "TipoBusqueda": TIPO_BUSQUEDA_DEVENGUE,
                "CodigoAFP": codigo,
                "PeriodoDevengueInicial": desde,
                "PeriodoDevengueFinal": hasta,
                "CodigoEstado": "",
                "FechaDeclaracionInicial": "",
                "FechaDeclaracionFinal": "",
                "FechaPagoInicial": "",
                "FechaPagoFinal": "",
                "ActualGuid": "",
                "pageNumber": "0",
            })
            encontradas.extend(parsers.parsear_planillas(html))
        return encontradas

    def resumen_situacion(self, desde: str, hasta: str) -> list[parsers.ResumenDevengue]:
        """Cuántas obligaciones de pago hay por devengue, y en qué estado."""
        html = self._post(
            RUTA_RESUMEN_OP,
            {
                "Presunta": "x",
                "Deuda": "x",
                "DevengueInicio": desde,
                "DevengueFin": hasta,
                "pageNumber": "0",
                "ActualGuid": "",
            },
            ruta_token=RUTA_PAGINA_RESUMEN,
        )
        return parsers.parsear_resumen_situacion(html)

    def deudas(self, tipo: str = "A") -> parsers.ReporteDeuda:
        """El reporte de deudas ciertas y presuntas («A»: todas).

        Va como multipart y sin token: es el único que no lo pide.
        """
        try:
            respuesta = self._sesion.post(
                f"{client.BASE}{RUTA_DEUDAS}",
                files={"tipoDeuda": (None, tipo)},
                timeout=client.TIMEOUT,
                headers={"X-Requested-With": "XMLHttpRequest", "Origin": client.BASE},
            )
            respuesta.raise_for_status()
        except requests.RequestException as exc:
            raise PortalError(f"Falló el reporte de deudas: {exc}") from exc
        client.comprobar_abierto(respuesta.text)
        return parsers.parsear_deudas(respuesta.text)

    def consultar_afiliado(self, documento: str, tipo_documento: str = "0"):
        """Busca un trabajador por documento y devuelve su ficha, o None.

        Además de devolverla, **fija ese afiliado en la sesión**: es lo que hace
        que ``historial_aportes`` hable de él y no de otro.
        """
        html = self._post(RUTA_AFILIADO, {
            "TipoFiltro": "D",
            "TipoDocumento": tipo_documento,
            "NumeroDocumento": documento,
            "pageNumber": "0",
            "ActualGuid": "",
        })
        return parsers.parsear_afiliado(html)

    def historial_aportes(self) -> list[parsers.AporteMensual]:
        """El historial mes a mes del afiliado fijado en la sesión.

        Depende del estado de la sesión, no de un parámetro: hay que llamar
        antes a ``consultar_afiliado``, o se estará leyendo el historial de
        quien se consultara la última vez. Exige ``Content-Length: 0``
        explícito o el portal responde 411.
        """
        try:
            respuesta = self._sesion.post(
                f"{client.BASE}{RUTA_HISTORIAL}",
                timeout=client.TIMEOUT,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Length": "0",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": client.BASE,
                },
            )
            respuesta.raise_for_status()
        except requests.RequestException as exc:
            raise PortalError(f"Falló el historial de aportes: {exc}") from exc
        try:
            return parsers.parsear_historial_aportes(respuesta.text)
        except ValueError as exc:
            raise client.SesionCaducada(
                "AFPnet no devolvió el historial; la sesión pudo caducar."
            ) from exc


def _buscar_ruc_encriptado(html: str) -> str:
    import re

    encontrado = re.search(
        r'RucEmpresaEncriptado"[^>]*value="([^"]*)"', html
    )
    return encontrado.group(1) if encontrado else ""


def anios_hasta_hoy(desde_anio: int, hoy: date | None = None) -> list[tuple[str, str]]:
    """Rangos (enero, diciembre) por año, para recorrer el histórico.

    El último año se corta en el mes actual: pedir devengues futuros no falla,
    pero devuelve vacío y gasta una consulta por AFP.
    """
    hoy = hoy or date.today()
    rangos = []
    for anio in range(desde_anio, hoy.year + 1):
        fin = 12 if anio < hoy.year else hoy.month
        rangos.append((f"{anio}01", f"{anio}{fin:02d}"))
    return rangos
