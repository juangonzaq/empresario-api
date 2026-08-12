"""Entrada a AFPnet: dos pasos, con una persona en medio.

El login lleva un CAPTCHA de imagen. **No se resuelve automáticamente y no se
va a resolver**: es un control anti-bots de AFPnet, y saltárselo pondría en
riesgo el acceso de la empresa a la plataforma con la que paga las pensiones de
sus trabajadores. Lo resuelve quien está delante de la pantalla.

De ahí que el login sea un desafío en dos tiempos:

1. ``pedir_desafio()`` — abre la sesión, se trae la página de login y devuelve
   la imagen del CAPTCHA junto con el estado que hará falta para completarlo.
2. ``responder_desafio(...)`` — reenvía el formulario con lo que la persona
   escribió y, si entra, devuelve las cookies de la sesión abierta.

Entre los dos pasos no hay ningún navegador vivo: la imagen viaja incrustada en
el propio HTML como ``data:`` URI, así que todo el intercambio cabe en
``requests`` y el estado intermedio es un diccionario serializable. Eso es lo
que permite que el paso 1 lo atienda un proceso web y el paso 2 otro distinto.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

BASE = "https://www.afpnet.com.pe"
LOGIN_URL = f"{BASE}/Empleador/IniciarSesion"

# El portal está detrás de un WAF que mira las cabeceras. Se manda lo que
# mandaría un navegador real, que es exactamente lo que hay al otro lado: una
# persona resolviendo el CAPTCHA.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
}

TIPO_USUARIO_EMPLEADOR = "2"
TIMEOUT = 45

# ── Extracción de la página de login ──
RE_TOKEN = re.compile(
    r'name="__RequestVerificationToken"[^>]*value="([^"]+)"'
)
RE_CAPTCHA_VALIDATE = re.compile(r'id="CaptchaValidate"[^>]*value="([^"]*)"')
RE_CAPTCHA_IMG = re.compile(r'id="CaptchaImg"[^>]*src="(data:image/[^"]+)"')
# La página de login se reconoce por su formulario; si vuelve, no entramos.
MARCA_LOGIN = 'id="frm-inicio-sesion"'

# AFPnet cierra por las noches y devuelve una pantalla propia —con HTTP 200— en
# lugar de un 503. Sin distinguirla, su horario se leía como «el portal cambió»,
# que manda a alguien a depurar un problema que no existe.
MARCA_MANTENIMIENTO = "sistema no disponible"
RE_HORARIO = re.compile(
    r"disponible todos los d[íi]as desde las .{0,60}?\.?\s?m\.", re.I
)


class AfpnetError(RuntimeError):
    """Cualquier fallo hablando con AFPnet."""


class DesafioCaducado(AfpnetError):
    """El desafío ya no vale: el CAPTCHA cambió o pasó demasiado tiempo."""


class PortalCerrado(AfpnetError):
    """AFPnet está fuera de su horario de atención.

    No es una avería ni un cambio de la web: cierra todas las noches. Importa
    distinguirlo porque condiciona cuándo puede correr la sincronización —un
    reparto nocturno encontraría el portal cerrado siempre—.
    """


class LoginRechazado(AfpnetError):
    """AFPnet no aceptó el intento. ``motivo`` distingue por qué."""

    def __init__(self, mensaje: str, *, captcha: bool = False):
        super().__init__(mensaje)
        #: True cuando lo que falló fue el CAPTCHA y no las credenciales: uno
        #: se reintenta con otra imagen, el otro exige corregir la clave.
        self.captcha = captcha


class SesionCaducada(AfpnetError):
    """Una petición autenticada volvió a la pantalla de login."""


@dataclass
class Desafio:
    """Lo que hay que enseñarle a la persona, y lo que hay que recordar.

    ``estado`` es opaco para quien lo transporta y **no contiene secretos de la
    empresa**: son las cookies anónimas de la visita y los tokens del
    formulario. Aun así viaja cifrado hasta la caché, porque con él se puede
    consumir el intento de login de esa visita.
    """

    #: La imagen tal cual viene del portal, lista para un `<img src=...>`.
    captcha_data_uri: str
    estado: dict[str, str] = field(repr=False)


def _texto_visible(html: str) -> str:
    limpio = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    limpio = re.sub(r"<[^>]+>", " ", limpio)
    return re.sub(r"\s+", " ", html_lib.unescape(limpio)).strip()


def comprobar_abierto(html: str) -> None:
    """Corta con un motivo claro si el portal está en su ventana de cierre.

    El horario se cita del propio portal en lugar de codificarlo aquí: si lo
    cambian, el mensaje sigue siendo cierto sin tocar nada.
    """
    texto = _texto_visible(html)
    if MARCA_MANTENIMIENTO not in texto.lower():
        return
    horario = RE_HORARIO.search(texto)
    detalle = f" AFPnet está {horario.group(0)}" if horario else ""
    raise PortalCerrado(
        f"AFPnet está cerrado en este momento.{detalle}".strip()
    )


def limpiar_data_uri(uri: str) -> str:
    """Deja el ``data:`` URI del CAPTCHA en condiciones de decodificarse.

    AFPnet sirve la imagen incrustada, y en medio del base64 aparecen entidades
    HTML —``&#xD;``— y saltos de línea del propio formateo de la página. El
    navegador los digiere sin pestañear; ``base64.b64decode`` no, y revienta con
    «Incorrect padding», que no se parece en nada a la causa.
    """
    limpio = html_lib.unescape(uri)
    return re.sub(r"\s+", "", limpio)


def _sesion_http(cookies: dict[str, str] | None = None) -> requests.Session:
    sesion = requests.Session()
    sesion.headers.update(HEADERS)
    for nombre, valor in (cookies or {}).items():
        sesion.cookies.set(nombre, valor, domain="www.afpnet.com.pe")
    return sesion


def pedir_desafio() -> Desafio:
    """Abre una visita a AFPnet y devuelve su CAPTCHA sin resolver."""
    sesion = _sesion_http()
    try:
        respuesta = sesion.get(LOGIN_URL, timeout=TIMEOUT)
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise AfpnetError(f"No pudimos abrir AFPnet: {exc}") from exc

    html = respuesta.text
    # Antes de buscar el formulario: si está cerrado, no hay formulario que
    # buscar y el error debe decir eso y no «la pantalla cambió».
    comprobar_abierto(html)

    token = RE_TOKEN.search(html)
    imagen = RE_CAPTCHA_IMG.search(html)
    validate = RE_CAPTCHA_VALIDATE.search(html)

    if not token or not imagen:
        # Si AFPnet rehace la pantalla, esto salta aquí y no veinte líneas más
        # abajo con un error que no se parece a la causa.
        raise AfpnetError(
            "La pantalla de login de AFPnet no tiene la forma que esperábamos. "
            "Puede que la hayan cambiado."
        )

    return Desafio(
        captcha_data_uri=limpiar_data_uri(imagen.group(1)),
        estado={
            "token": html_lib.unescape(token.group(1)),
            "captcha_validate": (
                html_lib.unescape(validate.group(1)) if validate else ""
            ),
            "cookies": dict(sesion.cookies),
        },
    )


def _motivo_del_rechazo(html: str) -> tuple[str, bool]:
    """Qué salió mal, y si fue culpa del CAPTCHA.

    AFPnet devuelve la misma pantalla con un mensaje dentro en lugar de un
    código de estado distinto, así que hay que leerlo del cuerpo.
    """
    texto = re.sub(r"<[^>]+>", " ", html)
    texto = re.sub(r"\s+", " ", texto).strip()

    for patron, es_captcha in (
        (r"[^.]*c[óo]digo de (?:la )?imagen[^.]*", True),
        (r"[^.]*captcha[^.]*", True),
        (r"[^.]*(?:usuario|contrase[ñn]a)[^.]*(?:incorrect|inv[áa]lid)[^.]*", False),
        (r"[^.]*(?:bloquead|desactivad)[^.]*", False),
    ):
        encontrado = re.search(patron, texto, re.I)
        if encontrado:
            return encontrado.group(0).strip()[:200], es_captcha

    return (
        "AFPnet no aceptó el inicio de sesión y no dijo por qué.",
        False,
    )


def responder_desafio(
    estado: dict,
    ruc: str,
    usuario: str,
    clave: str,
    captcha: str,
) -> dict[str, str]:
    """Completa el login con el texto que escribió la persona.

    Devuelve las cookies de la sesión abierta. Ni la clave ni el texto del
    CAPTCHA se guardan en ningún sitio: se usan aquí y se descartan.
    """
    if not estado or "token" not in estado:
        raise DesafioCaducado(
            "El desafío ya no vale. Pide una imagen nueva y vuelve a intentarlo."
        )

    sesion = _sesion_http(estado.get("cookies"))
    formulario = {
        "TipoUsuario": TIPO_USUARIO_EMPLEADOR,
        "NumeroDocumento": ruc,
        "NombreUsuario": usuario,
        "Contrasenia": clave,
        "Captcha": captcha,
        "CaptchaValidate": estado.get("captcha_validate", ""),
        "Fantasma": "",
        "__RequestVerificationToken": estado["token"],
    }

    try:
        respuesta = sesion.post(
            LOGIN_URL,
            data=formulario,
            timeout=TIMEOUT,
            headers={"Referer": LOGIN_URL, "Origin": BASE},
            allow_redirects=True,
        )
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise AfpnetError(f"No pudimos enviar el formulario: {exc}") from exc

    # Volver a ver el formulario de login es la señal de que no entramos.
    if MARCA_LOGIN in respuesta.text:
        motivo, es_captcha = _motivo_del_rechazo(respuesta.text)
        logger.info("AFPnet rechazó el login de %s: %s", ruc, motivo)
        raise LoginRechazado(motivo, captcha=es_captcha)

    cookies = dict(sesion.cookies)
    if not cookies:
        raise AfpnetError("AFPnet no devolvió sesión tras aceptar el login.")

    logger.info("Sesión AFPnet abierta para %s", ruc)
    return cookies


def sesion_autenticada(cookies: dict[str, str]) -> requests.Session:
    """Una sesión HTTP con las cookies ya abiertas, para leer el portal."""
    return _sesion_http(cookies)


# Endpoint con el que se comprueba que la sesión sigue abierta. Se eligió éste
# porque devuelve JSON: `/Empleador` —que parecía lo natural— es la pantalla
# **pública** de login, así que daba por caducada cualquier sesión, incluso una
# recién abierta. Exige `Content-Length: 0` explícito o responde 411.
URL_SESION_VIVA = f"{BASE}/GestionarObligacionPago/Afiliado/getSessionOpListJson"

# A donde manda AFPnet cuando la sesión expiró por inactividad.
RUTA_TIEMPO_AGOTADO = "/Seguridad/RequestTimeout"


def comprobar_sesion(cookies: dict[str, str]) -> None:
    """Confirma que la sesión sigue abierta, o dice por qué no.

    No vale mirar el código de estado: AFPnet responde 200 tanto con datos como
    con la pantalla de login. Lo que distingue una sesión viva es que el cuerpo
    sea JSON.
    """
    sesion = sesion_autenticada(cookies)
    try:
        respuesta = sesion.post(
            URL_SESION_VIVA,
            timeout=TIMEOUT,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Length": "0",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE,
            },
        )
        # Una sesión expirada no vuelve al login: AFPnet redirige a su propia
        # pantalla de tiempo agotado y responde 408. Tratarlo como error de red
        # haría que la interfaz dijera «no pudimos consultar AFPnet» cuando lo
        # que hay que hacer es volver a entrar.
        if respuesta.status_code == 408 or RUTA_TIEMPO_AGOTADO in respuesta.url:
            raise SesionCaducada(
                "La sesión de AFPnet caducó por inactividad. Vuelve a iniciarla."
            )
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise AfpnetError(f"No pudimos consultar AFPnet: {exc}") from exc

    comprobar_abierto(respuesta.text)
    try:
        json.loads(respuesta.text)
    except ValueError as exc:
        raise SesionCaducada(
            "La sesión de AFPnet caducó. Hay que volver a iniciarla."
        ) from exc


def comprobar_viva(cookies: dict[str, str], url: str) -> str:
    """Pide una página del portal y confirma que la sesión sigue abierta.

    Que una petición devuelva 200 no basta: AFPnet contesta 200 con la pantalla
    de login cuando la sesión caducó, así que se mira el cuerpo.
    """
    sesion = sesion_autenticada(cookies)
    try:
        respuesta = sesion.get(url, timeout=TIMEOUT)
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise AfpnetError(f"No pudimos consultar AFPnet: {exc}") from exc

    # Un portal cerrado no significa que la sesión haya muerto: si se
    # confundieran, cada noche se borrarían sesiones que al día siguiente
    # habrían servido, y alguien tendría que resolver un CAPTCHA de más.
    comprobar_abierto(respuesta.text)

    if MARCA_LOGIN in respuesta.text:
        raise SesionCaducada(
            "La sesión de AFPnet caducó. Hay que volver a iniciarla."
        )
    return respuesta.text
