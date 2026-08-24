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


def login_form(ruc: str, username: str, password: str) -> dict:
    """El formulario de login SOL listo para enviarse desde el navegador.

    Los nombres de campo son los de ``LoginForm`` en la página de SUNAT:
    ``tipo=2`` es «entrar con RUC» (1 sería con DNI), ``captcha`` va vacío
    porque SUNAT solo lo pide tras varios intentos fallidos, y ``originalUrl``,
    ``lang`` y ``state`` son los que la página copia de su propia URL.
    """
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
