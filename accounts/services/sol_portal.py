"""Abrir SUNAT Operaciones en Línea con la sesión ya iniciada.

El login de SOL es un formulario HTML que se envía por POST a ``j_security_check``
desde ``api-seguridad.sunat.gob.pe``: no exige cookies previas ni token CSRF, y
el navegador de la persona puede enviarlo igual que lo haría la página de SUNAT
si lo tuviera relleno. Eso es lo que hace este módulo: devolver al frontend el
formulario exactamente como lo construiría la página de SUNAT —misma acción,
mismos campos— para que lo envíe en una pestaña nueva y aterrice dentro del
menú SOL sin teclear nada.

El único campo que no es fijo ni nuestro es ``state``: un HashMap de Java
serializado y en base64 que el menú SOL genera a partir de la URL que se quiere
abrir (``pestana=*&agrupacion=*``) y que SUNAT devuelve al navegador tras el
login para saber dónde dejarlo. Es determinista para una misma URL, pero lleva
un hash que SUNAT podría cambiar, así que se lee fresco del portal y se cachea;
si SUNAT no contesta a tiempo, vale el último que se conoció.

Es el único sitio, junto con los scrapers, donde la clave SOL vuelve a texto
claro. Sale de aquí solo para ir directamente a SUNAT, y lo pide quien la
entregó o quien administra la empresa; nunca un usuario de solo lectura.
"""

from __future__ import annotations

import logging
import re

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

SOL_CLIENT_ID = "4f3b88b3-d9d6-402a-b85d-6a0bc857746a"
LOGIN_ACTION = (
    "https://api-seguridad.sunat.gob.pe/v1/clientessol/"
    f"{SOL_CLIENT_ID}/oauth2/j_security_check"
)
# Adónde SUNAT manda el navegador tras autenticar: el menú canjea el código
# OAuth2 y abre la sesión.
ORIGINAL_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/AutenticaMenuInternet.htm"
# La pantalla a la que se quiere llegar: el menú completo, todas las pestañas.
MENU_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm?pestana=*&agrupacion=*"

# ``state`` tal como lo generaba el menú para MENU_URL el 2026-08-20. Es el
# respaldo para cuando el portal no contesta; mientras conteste, se usa el suyo.
FALLBACK_STATE = (
    "rO0ABXNyABFqYXZhLnV0aWwuSGFzaE1hcAUH2sHDFmDRAwACRgAKbG9hZEZhY3RvckkACXRocmVzaG9sZHhwP0AAAAAAAAx3CAAAABAAAAADdAADZXhlcHQABnBhcmFtc3QASyomKiYvY2wtdGktaXRtZW51L01lbnVJbnRlcm5ldC5odG0mYjY0ZDI2YThiNWFmMDkxOTIzYjIzYjY0MDdhMWMxZGI0MWU3MzNhNnQABGV4ZWNweA=="
)

STATE_CACHE_KEY = "sunat_sol_portal_state"
STATE_CACHE_SECONDS = 60 * 60
FETCH_TIMEOUT_SECONDS = 5
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

_STATE_IN_REDIRECT = re.compile(r"[?&]state=([A-Za-z0-9+/=]+)")


def fetch_menu_state() -> str | None:
    """El ``state`` que el menú SOL genera hoy para MENU_URL, o None si no se
    pudo leer. Sin sesión, el menú responde una página mínima que redirige por
    JavaScript al login; el ``state`` va en esa URL de redirección."""
    try:
        response = requests.get(
            MENU_URL, timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("No se pudo leer el state del menú SOL: %s", exc)
        return None
    match = _STATE_IN_REDIRECT.search(response.text)
    return match.group(1) if match else None


def menu_state() -> str:
    """El ``state`` vigente, cacheado una hora; el de respaldo si no hay otro."""
    cached = cache.get(STATE_CACHE_KEY)
    if cached:
        return cached
    fresh = fetch_menu_state()
    if fresh:
        cache.set(STATE_CACHE_KEY, fresh, STATE_CACHE_SECONDS)
        return fresh
    return FALLBACK_STATE


# ── Destinos ────────────────────────────────────────────────────────────────
# El acceso a SUNAT se desglosa en tres. Cada uno entra por su propio cliente
# OAuth2 de SUNAT: el menú clásico (trámites, consultas y también el grupo
# «Mis declaraciones y pagos») y e-renta, que es otra aplicación con su login.
DESTINO_TRAMITES = "tramites"
DESTINO_DECLARACIONES = "declaraciones"
DESTINO_RENTA = "renta"
DESTINOS = (DESTINO_TRAMITES, DESTINO_DECLARACIONES, DESTINO_RENTA)

# e-renta construye en JavaScript esta URL de autorización; al pedirla sin
# sesión, SUNAT redirige a su página de login con ``originalUrl`` y ``state``
# ya resueltos. Ese es el formulario que se reproduce.
RENTA_CLIENT_ID = "03590141-c69c-438c-a36a-8ee2a3ad9747"
RENTA_REDIRECT = "https://e-renta.sunat.gob.pe/loader/recaudaciontributaria/declaracionpago/formularios"
RENTA_AUTHEN_URL = (
    f"https://api-seguridad.sunat.gob.pe/v1/clientessol/{RENTA_CLIENT_ID}/oauth2/authen"
    f"?response_type=code&client_id={RENTA_CLIENT_ID}&scope=/e-renta"
    f"&redirect_uri={RENTA_REDIRECT}"
)


class PortalUnavailable(RuntimeError):
    """El portal no devolvió su página de login: no se puede armar el formulario."""


def oauth_login_form(
    authen_url: str, ruc: str, username: str, password: str,
    session: requests.Session | None = None, timeout: int = FETCH_TIMEOUT_SECONDS,
) -> dict:
    """Formulario de login para cualquier aplicación que delega en Clave SOL.

    Se pide la URL de autorización sin seguir la redirección: la ``Location``
    es la página de login de SUNAT y lleva en su query ``originalUrl``,
    ``state`` y ``lang``, que son justo los campos que la página copia en su
    formulario. La acción es ``j_security_check`` bajo el mismo cliente.
    """
    http = session or requests
    try:
        response = http.get(
            authen_url, allow_redirects=False, timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise PortalUnavailable(str(exc)) from exc
    login_url = response.headers.get("Location", "")
    if "/oauth2/" not in login_url:
        raise PortalUnavailable(f"sin redirección de login ({response.status_code})")
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(login_url).query)
    oauth_base = login_url.split("/oauth2/")[0] + "/oauth2"
    return {
        "action": f"{oauth_base}/j_security_check",
        "method": "POST",
        "fields": {
            "tipo": "2",
            "dni": "",
            "custom_ruc": ruc,
            "j_username": username,
            "j_password": password,
            "captcha": "",
            "originalUrl": query.get("originalUrl", [""])[0],
            "lang": query.get("lang", ["es-PE"])[0],
            "state": query.get("state", [""])[0],
        },
    }


def login_form(ruc: str, username: str, password: str, destino: str = DESTINO_TRAMITES) -> dict:
    """El formulario de login SOL listo para enviarse desde el navegador.

    ``destino`` elige la aplicación: el menú clásico para trámites, consultas
    y declaraciones, o e-renta para la declaración anual.

    Los nombres de campo son los de ``LoginForm`` en la página de SUNAT:
    ``tipo=2`` es «entrar con RUC» (1 sería con DNI), ``captcha`` va vacío
    porque SUNAT solo lo pide tras varios intentos fallidos, y ``originalUrl``,
    ``lang`` y ``state`` son los que la página copia de su propia URL.
    """
    if destino == DESTINO_RENTA:
        return oauth_login_form(RENTA_AUTHEN_URL, ruc, username, password)
    return {
        "action": LOGIN_ACTION,
        "method": "POST",
        "fields": {
            "tipo": "2",
            "dni": "",
            "custom_ruc": ruc,
            "j_username": username,
            "j_password": password,
            "captcha": "",
            "originalUrl": ORIGINAL_URL,
            "lang": "es-PE",
            "state": menu_state(),
        },
    }
