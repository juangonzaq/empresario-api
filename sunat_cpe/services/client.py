"""Browser + HTTP client for SUNAT's Consultar Factura y Nota (all CPE types).

Same menu-signed session pattern as the electronic mailbox: log into SOL, open
the "Consultar Factura y Nota" option (menu 11.5.3.1.2) so SUNAT sets the
``ITCONSCPEMYPESEESESSION`` cookie, then hand the cookies to ``requests``. Each
(period, tipoConsulta) is queried through ``consultar.do?action=realizarConsulta``
(a JSON envelope), and each XML is pulled through ``action=descargarFactura``
(a ZIP whose only ``.XML`` member is the signed UBL document). XML download works
for both issued and received documents (the ``ruc`` field is the issuer's).
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from typing import Any

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from sunat_mailbox.services.constants import (
    ATTEMPT_POLL_MS,
    BROWSER_ARGS,
    DEFAULT_TIMEOUT_MS,
    USER_AGENT,
)

from .constants import (
    ACTION_DOWNLOAD_XML,
    ACTION_QUERY,
    APP_MARKER,
    CONSULTAR_URL,
    CPE_MENU_CODE,
    ESTADO_VALIDO,
    MENU_URL,
)
from .parsing import month_bounds, parse_records

logger = logging.getLogger(__name__)


class CpePortalError(RuntimeError):
    """Raised when the SOL login does not reach the CPE consulta."""


@dataclass
class CpePortalClient:
    """Authenticates against SUNAT SOL and reads electronic comprobantes."""

    taxpayer_id: str
    username: str
    password: str
    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    session: requests.Session = field(default_factory=requests.Session, init=False)
    signed_ref: str = field(default="", init=False)

    # ---------------------------------------------------------------- login
    def login(self) -> None:
        signed_urls: list[str] = []

        def sniff(request: Any) -> None:
            if APP_MARKER in request.url:
                signed_urls.append(request.url)

        with sync_playwright() as playwright:
            # SUNAT's WAF resets the connection for Playwright's headless shell, so the
            # full Chromium build is required alongside the automation flag being hidden.
            browser = playwright.chromium.launch(
                headless=self.headless, channel="chromium", args=BROWSER_ARGS
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale="es-PE",
                viewport={"width": 1440, "height": 900},
            )
            context.on("page", lambda page: page.on("request", sniff))
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            page.on("request", sniff)

            try:
                self._submit_credentials(page)
                self._open_consulta(page, signed_urls)
                if not signed_urls:
                    signed_urls = [
                        frame.url for frame in page.frames if APP_MARKER in frame.url
                    ]
                if not signed_urls:
                    raise CpePortalError(
                        "The CPE consulta never loaded. Check the credentials, or run "
                        "with --headful to see whether SUNAT changed the menu."
                    )
                self.signed_ref = signed_urls[0]
                cookies = context.cookies()
            except PlaywrightError as exc:
                raise CpePortalError(f"Browser automation failed: {exc}") from exc
            finally:
                browser.close()

        self.session = self._build_session(cookies)
        logger.info("CPE session ready for %s", self.taxpayer_id)

    def _submit_credentials(self, page: Any) -> None:
        page.goto(MENU_URL, wait_until="networkidle", timeout=self.timeout_ms)
        page.wait_for_selector("#txtRuc", state="visible", timeout=self.timeout_ms)
        page.click("#btnPorRuc")
        page.fill("#txtRuc", self.taxpayer_id)
        page.fill("#txtUsuario", self.username)
        page.fill("#txtContrasena", self.password)
        page.click("#btnAceptar")
        page.wait_for_url(lambda url: "loginMenuSol" not in url, timeout=self.timeout_ms)
        page.wait_for_load_state("networkidle", timeout=self.timeout_ms)

    def _open_consulta(self, page: Any, signed_urls: list[str]) -> None:
        page.evaluate(
            "(code) => ejecuta('MenuInternet.htm?action=iconExecute&code=' + code,"
            " false, 'Consultas Facturas y Notas', '#nivel1_11', code)",
            CPE_MENU_CODE,
        )
        for _ in range(self.timeout_ms // ATTEMPT_POLL_MS):
            if signed_urls:
                return
            page.wait_for_timeout(ATTEMPT_POLL_MS)

    def _build_session(self, cookies: list[dict[str, Any]]) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Referer": self.signed_ref})
        for cookie in cookies:
            session.cookies.set(
                cookie["name"], cookie["value"],
                domain=cookie["domain"], path=cookie.get("path", "/"),
            )
        return session

    # ----------------------------------------------------------------- read
    def query(
        self, period: str, tipo_consulta: str, *, estado: str = ESTADO_VALIDO
    ) -> list[dict[str, Any]]:
        """Return the raw records for a period and a tipoConsulta (10..16)."""
        if not self.signed_ref:
            raise CpePortalError("login() must be called before query().")
        fec_desde, fec_hasta = month_bounds(period)
        response = self.session.get(
            CONSULTAR_URL,
            params={
                "action": ACTION_QUERY,
                "buscarPor": "porPer",
                "estado": estado,
                "fec_desde": fec_desde,
                "fec_hasta": fec_hasta,
                "tipoConsulta": tipo_consulta,
            },
            timeout=90,
        )
        response.raise_for_status()
        return parse_records(response.text)

    def download_xml(self, fields: dict[str, Any]) -> tuple[str, str]:
        """Download one comprobante's signed XML. Returns (filename, xml_text).

        Works for issued and received documents: ``ruc`` is the issuer's RUC and
        ``tipo`` is codFactura (the value the download form expects, which differs
        from tipoCPE for notes).
        """
        response = self.session.post(
            CONSULTAR_URL,
            data={
                "action": ACTION_DOWNLOAD_XML,
                "ruc": fields["issuer_ruc"],
                "tipo": fields["download_code"] or fields["document_type"],
                "serie": fields["series"],
                "numero": fields["number"],
            },
            timeout=120,
        )
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            xml_name = next(
                (n for n in archive.namelist() if n.upper().endswith(".XML")), None
            )
            if not xml_name:
                raise CpePortalError(
                    f"No XML inside the download for {fields['full_number']}"
                )
            xml_bytes = archive.read(xml_name)
        return xml_name, xml_bytes.decode("ISO-8859-1")
