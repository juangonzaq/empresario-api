"""Abrir la casilla electrónica de SUNAFIL con la Clave SOL de la empresa.

SUNAFIL delega el ingreso del empleador en SUNAT: su página de entrada arma en
JavaScript una URL de autorización OAuth2 (cliente propio de SUNAFIL) y SUNAT
responde con su página de login. Es el mismo camino que recorre el
sincronizador de la casilla; aquí solo se reconstruye el formulario para que
lo envíe el navegador de la persona, igual que con el portal SOL.
"""

from __future__ import annotations

import logging

from sunafil.services.client import SunafilClient, SunafilError
from sunafil.services.constants import BASE_URL, ENTRY_PATH

from .sol_portal import PortalUnavailable, oauth_login_form

logger = logging.getLogger(__name__)

ENTRY_URL = BASE_URL + ENTRY_PATH
FETCH_TIMEOUT_SECONDS = 15


def login_form(ruc: str, username: str, password: str) -> dict:
    """Formulario de login a SUNAFIL, o ``PortalUnavailable`` si el portal no
    contesta. La sesión del cliente lleva el adaptador TLS que SUNAFIL exige."""
    client = SunafilClient(taxpayer_id=ruc, username=username, password=password)
    try:
        entry = client.session.get(ENTRY_URL, timeout=FETCH_TIMEOUT_SECONDS)
        entry.raise_for_status()
        authen_url = client._build_authen_url(entry.text)
    except SunafilError as exc:
        raise PortalUnavailable(str(exc)) from exc
    except Exception as exc:  # requests.RequestException y afines
        raise PortalUnavailable(f"SUNAFIL no respondió: {exc}") from exc
    return oauth_login_form(
        authen_url, ruc, username, password,
        session=client.session, timeout=FETCH_TIMEOUT_SECONDS,
    )
