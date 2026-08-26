"""Client for SUNAFIL's casilla electrónica.

Employer access is delegated to SUNAT's Clave SOL: the casilla bounces through the
same OAuth2 flow the tax mailbox uses, so the credentials in ``SUNAT_*`` are enough
and no browser is needed.

The app is JSF/PrimeFaces, so navigation and opening a detail are form posts carrying
``javax.faces.ViewState`` rather than plain links.
"""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .constants import (
    BASE_URL,
    ENTRY_PATH,
    LANDING_PATH,
    REQUEST_TIMEOUT,
    USER_AGENT,
    VIEW_STATE_FIELD,
    ListingSpec,
)

logger = logging.getLogger(__name__)


class SunafilError(RuntimeError):
    """The casilla could not be reached or read."""


class SunafilLoginError(SunafilError):
    """Authentication against the casilla failed."""


class LegacyTLSAdapter(HTTPAdapter):
    """SUNAFIL negotiates TLS1.2 with AES128-SHA.

    OpenSSL 3 rejects that at its default security level (no forward secrecy), so
    the handshake fails outright without lowering SECLEVEL for this host.
    """

    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context(ciphers="DEFAULT:@SECLEVEL=1")
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


@dataclass
class ListingPage:
    """A listing's parsed table plus the state needed to post back into it."""

    spec: ListingSpec
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    detail_button_ids: list[str] = field(default_factory=list)
    view_state: str = ""

    def records(self) -> list[dict[str, str]]:
        return [dict(zip(self.headers, row)) for row in self.rows]


@dataclass
class SunafilClient:
    taxpayer_id: str
    username: str
    password: str
    timeout: int = REQUEST_TIMEOUT

    session: requests.Session = field(default_factory=requests.Session, init=False)
    logged_in: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.session.mount("https://", LegacyTLSAdapter())

    # ---------------------------------------------------------------- login
    def login(self) -> None:
        """Authenticate through SUNAT Clave SOL and land on the casilla."""
        try:
            entry = self.session.get(BASE_URL + ENTRY_PATH, timeout=self.timeout)
            entry.raise_for_status()
            authen_url = self._build_authen_url(entry.text)

            redirect = self.session.get(
                authen_url, allow_redirects=False, timeout=self.timeout
            )
            login_url = redirect.headers.get("Location")
            if not login_url:
                # Fallo del portal, no de la clave: no debe marcar la
                # credencial como rechazada.
                raise SunafilError(
                    "SUNAT no devolvió la página de ingreso a SUNAFIL; "
                    "suele ser temporal."
                )
            self.session.get(login_url, timeout=self.timeout)

            query = parse_qs(urlparse(login_url).query)
            oauth_base = login_url.split("/oauth2/")[0] + "/oauth2"
            result = self.session.post(f"{oauth_base}/j_security_check", data={
                "tipo": "2", "dni": "",
                "custom_ruc": self.taxpayer_id,
                "j_username": self.username,
                "j_password": self.password,
                "captcha": "",
                "originalUrl": query.get("originalUrl", [""])[0],
                "lang": query.get("lang", ["es-PE"])[0],
                "state": query.get("state", [""])[0],
            }, headers={"Referer": login_url}, timeout=self.timeout)
        except requests.Timeout as exc:
            # El detalle técnico («HTTPSConnectionPool… read timeout») acababa
            # tal cual en la pantalla del usuario. Un portal que no responde
            # es un problema del portal, no de las credenciales.
            raise SunafilError(
                "SUNAFIL no respondió a tiempo. El portal se satura a ratos; "
                "reintenta en unos minutos."
            ) from exc
        except requests.RequestException as exc:
            raise SunafilError(
                "No se pudo conectar con SUNAFIL "
                f"({exc.__class__.__name__}). Reintenta en unos minutos."
            ) from exc

        if LANDING_PATH not in result.url:
            raise SunafilLoginError(
                "SUNAT no aceptó el ingreso con la clave SOL. Revisa las "
                "credenciales; tras varios intentos fallidos SUNAT puede "
                "además pedir un captcha."
            )
        self.logged_in = True
        logger.info("SUNAFIL casilla login succeeded for %s", self.taxpayer_id)

    def _build_authen_url(self, page: str) -> str:
        """The entry page builds the OAuth2 URL in JavaScript; rebuild it here."""
        def read(name: str) -> str:
            match = re.search(rf'var {name}\s*=\s*[\'"]([^\'"]+)[\'"]', page)
            if not match:
                # La página de entrada cambió: portal, no credenciales.
                raise SunafilError(
                    f"El portal de SUNAFIL cambió su página de ingreso "
                    f"(no se encontró «{name}»)."
                )
            return match.group(1)

        client_id = read("cid")
        return (
            f"{read('pathurl')}/v1/clientessol/{client_id}/oauth2/authen"
            f"?client_id={client_id}&response_type=code"
            f"&state={read('st')}&redirect_uri={read('redirectURL')}"
        )

    # -------------------------------------------------------------- reading
    def _get(self, path: str) -> str:
        if not self.logged_in:
            raise SunafilError("login() must be called first.")
        try:
            response = self.session.get(BASE_URL + path, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SunafilError(f"GET {path} failed: {exc}") from exc
        return response.text

    def fetch_listing(self, spec: ListingSpec) -> ListingPage:
        from .parsers import parse_listing

        return parse_listing(spec, self._get(spec.path))

    def fetch_orientation_detail(self, spec: ListingSpec, button_id: str,
                                 view_state: str) -> str:
        """Open one orientation.

        This marks the item as read on SUNAFIL's side, which is why only the
        orientation listing exposes it: for requirements and notifications the same
        acknowledgement starts legal deadlines.
        """
        if not spec.detail_is_safe:
            raise SunafilError(
                f"Opening a {spec.kind} detail would acknowledge receipt on SUNAFIL "
                f"and start legal deadlines; refusing to do it automatically."
            )
        try:
            response = self.session.post(BASE_URL + spec.path, data={
                spec.form_id: spec.form_id,
                button_id: button_id,
                VIEW_STATE_FIELD: view_state,
            }, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SunafilError(f"Opening detail {button_id} failed: {exc}") from exc
        return response.text

    def view_state_of(self, page: str) -> str:
        field = BeautifulSoup(page, "html.parser").find(
            "input", {"name": VIEW_STATE_FIELD}
        )
        return field["value"] if field else ""
