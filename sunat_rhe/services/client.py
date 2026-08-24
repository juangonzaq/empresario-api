"""Browser + HTTP client for SUNAT's received fee receipts (RHE) query.

Same recipe as the CPE and ITF scrapers: Playwright logs into SOL and
fires menu option 11.5.1.1.14 («Consulta de recibos» with the company as
user of the service), which makes SUNAT sign the
``ol-ti-itreciboelectronico`` session; the cookies move to ``requests``
and the criteria form is posted directly — captured from a live session,
see ``constants.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from core.browser import browser_env
from sunat_cpe.services.parsing import month_bounds
from sunat_mailbox.services.constants import (
    ATTEMPT_POLL_MS,
    BROWSER_ARGS,
    DEFAULT_TIMEOUT_MS,
    USER_AGENT,
)

from .constants import (
    ACTION_DETAIL,
    APP_MARKER,
    APP_URL,
    MENU_URL,
    QUERY_BASE,
    QUERY_TYPES,
    RHE_MENU_CODE,
)
from .parsing import detail_from_html, rows_from_html

logger = logging.getLogger(__name__)


class RhePortalError(RuntimeError):
    """Raised when the SOL login never reaches the fee-receipt query."""


@dataclass
class RhePortalClient:
    """Authenticates against SOL and queries received fee receipts.

    Usage::

        client = RhePortalClient(taxpayer_id, username, password)
        rows_by_period = client.collect(["202607", "202608"])
    """

    taxpayer_id: str
    username: str
    password: str
    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    session: requests.Session = field(default_factory=requests.Session, init=False)
    signed_url: str = field(default="", init=False)

    # ---------------------------------------------------------------- flow
    def collect(self, periods: list[str]) -> dict[str, list[dict[str, str]]]:
        self._login()
        results: dict[str, list[dict[str, str]]] = {}
        for period in periods:
            rows = self._query(period)
            # The detail action is stateful: ``posirecibo`` indexes the
            # LAST executed listing, so details are fetched right here,
            # before the next period replaces it.
            for index, row in enumerate(rows):
                row["__detail__"] = self._detail(index)
            results[period] = rows
        return results

    # --------------------------------------------------------------- login
    def _login(self) -> None:
        if self.signed_url:
            return  # session already minted: backfill reuses one login
        signed_urls: list[str] = []

        def sniff(request: Any) -> None:
            if APP_MARKER in request.url:
                signed_urls.append(request.url)

        with sync_playwright() as playwright:
            # SUNAT's WAF resets the headless shell; the full Chromium with
            # the automation flag hidden is what every scraper here uses.
            browser = playwright.chromium.launch(
                headless=self.headless, channel="chromium", args=BROWSER_ARGS,
                env=browser_env(),
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale="es-PE",
                viewport={"width": 1440, "height": 900},
            )
            context.on("page", lambda page: page.on("request", sniff))
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            try:
                self._submit_credentials(page)
                self._open_option(page, signed_urls)
                if not signed_urls:
                    signed_urls = [
                        frame.url for frame in page.frames
                        if APP_MARKER in frame.url
                    ]
                if not signed_urls:
                    raise RhePortalError(
                        "La consulta de recibos por honorarios (opción "
                        f"{RHE_MENU_CODE}) no cargó. Si la clave SOL es un "
                        "usuario secundario, dale acceso a esa opción desde "
                        "Gestión de Usuarios Secundarios."
                    )
                self.signed_url = signed_urls[0]
                cookies = context.cookies()
            except PlaywrightError as exc:
                raise RhePortalError(
                    f"El navegador falló contra el portal SOL: {exc}"
                ) from exc
            finally:
                browser.close()

        self.session = self._build_session(cookies)
        logger.info("RHE session ready for %s", self.taxpayer_id)

    def _submit_credentials(self, page: Any) -> None:
        page.goto(MENU_URL, wait_until="networkidle", timeout=self.timeout_ms)
        page.wait_for_selector("#txtRuc", state="visible", timeout=self.timeout_ms)
        page.click("#btnPorRuc")
        page.fill("#txtRuc", self.taxpayer_id)
        page.fill("#txtUsuario", self.username)
        page.fill("#txtContrasena", self.password)
        page.click("#btnAceptar")
        page.wait_for_url(
            lambda url: "loginMenuSol" not in url, timeout=self.timeout_ms
        )
        page.wait_for_load_state("networkidle", timeout=self.timeout_ms)

    def _open_option(self, page: Any, signed_urls: list[str]) -> None:
        """Fire the menu option the way the SOL menu itself does."""
        page.evaluate(
            "(code) => ejecuta('MenuInternet.htm?action=iconExecute&code=' + code,"
            " false, 'Consulta de recibos', '#nivel1_11', code)",
            RHE_MENU_CODE,
        )
        for _ in range(self.timeout_ms // ATTEMPT_POLL_MS):
            if signed_urls:
                return
            page.wait_for_timeout(ATTEMPT_POLL_MS)

    def _build_session(self, cookies: list[dict[str, Any]]) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://ww1.sunat.gob.pe",
            "Referer": self.signed_url,
        })
        for cookie in cookies:
            session.cookies.set(
                cookie["name"], cookie["value"],
                domain=cookie["domain"], path=cookie.get("path", "/"),
            )
        return session

    # ----------------------------------------------------------------- read
    def _query(self, period: str) -> list[dict[str, str]]:
        """POST the captured criteria form for one yyyymm period."""
        if not self.signed_url:
            raise RhePortalError("login must succeed before querying")
        start, end = month_bounds(period)
        response = self.session.post(
            APP_URL,
            data={
                **QUERY_BASE,
                "fec_desde": start,
                "fec_hasta": end,
                "tipocomprobante1": QUERY_TYPES,
            },
            timeout=90,
        )
        response.raise_for_status()
        # The JSP answers in Latin-1; requests guesses wrong and the
        # names lose their accents.
        response.encoding = "iso-8859-1"
        return rows_from_html(response.text)

    def _detail(self, index: int) -> dict:
        """One receipt's detail page (concept, payments), by its position
        in the listing just queried. Fail-soft: a detail that does not
        parse must not sink the whole sync."""
        try:
            response = self.session.post(
                APP_URL,
                data={"posirecibo": str(index), "accion": ACTION_DETAIL},
                timeout=90,
            )
            response.raise_for_status()
            response.encoding = "iso-8859-1"
            return detail_from_html(response.text)
        except Exception:  # pragma: no cover — defensive
            logger.exception("RHE: detalle %d no se pudo leer", index)
            return {}
