"""Browser + HTTP client for SUNAT's Consulta de ITF.

The report at ww1.sunat.gob.pe/cl-at-itconitf/sci01Alias is guarded by the same
menu-signed params (``p``/``tenc``/``prg``/``usub``) as the electronic mailbox,
so authentication needs a real browser: log into SOL, trigger the "Consulta de
ITF" menu option (code 13.6.1.1.1), and sniff the signed report URL off the
request the menu fires. The session cookies are then handed to ``requests`` and
the report form is posted directly, which is faster and steadier than the DOM.
"""

from __future__ import annotations

import logging
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
    ACCION_QUERY,
    DOC_TYPE_RUC,
    ITF_MENU_CODE,
    MENU_URL,
    REPORT_MARKER,
    REPORT_URL,
)

logger = logging.getLogger(__name__)


class ItfPortalError(RuntimeError):
    """Raised when the SOL login does not reach the ITF report."""


@dataclass
class ItfPortalClient:
    """Authenticates against SUNAT SOL and posts the Consulta de ITF form.

    Usage::

        client = ItfPortalClient(taxpayer_id, username, password)
        client.login()
        html = client.fetch_report("202601", "202608")
    """

    taxpayer_id: str
    username: str
    password: str
    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    session: requests.Session = field(default_factory=requests.Session, init=False)
    report_url: str = field(default="", init=False)

    # ---------------------------------------------------------------- login
    def login(self) -> None:
        """Drive the browser to the ITF option and capture its signed URL."""
        signed_urls: list[str] = []

        def sniff(request: Any) -> None:
            if REPORT_MARKER in request.url:
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
                self._open_itf(page, signed_urls)
                if not signed_urls:
                    signed_urls = [
                        frame.url for frame in page.frames
                        if REPORT_MARKER in frame.url
                    ]
                if not signed_urls:
                    raise ItfPortalError(
                        "The ITF report never loaded. Check the credentials, or run "
                        "with --headful to see whether SUNAT changed the menu."
                    )
                self.report_url = signed_urls[0]
                cookies = context.cookies()
            except PlaywrightError as exc:
                raise ItfPortalError(f"Browser automation failed: {exc}") from exc
            finally:
                browser.close()

        self.session = self._build_session(cookies)
        logger.info("ITF session ready for %s", self.taxpayer_id)

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

    def _open_itf(self, page: Any, signed_urls: list[str]) -> None:
        """Fire the menu option the way the SOL menu does, then wait for its URL."""
        page.evaluate(
            "(code) => ejecuta('MenuInternet.htm?action=iconExecute&code=' + code,"
            " false, 'Consulta de ITF', '#nivel1_13', code)",
            ITF_MENU_CODE,
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
            "Referer": self.report_url,
        })
        for cookie in cookies:
            session.cookies.set(
                cookie["name"], cookie["value"],
                domain=cookie["domain"], path=cookie.get("path", "/"),
            )
        return session

    # ----------------------------------------------------------------- read
    def fetch_report(self, period_start: str, period_end: str) -> str:
        """POST the ITF form for a period range and return the results HTML."""
        if not self.report_url:
            raise ItfPortalError("login() must be called before fetch_report().")
        response = self.session.post(
            REPORT_URL,
            data={
                "tipdocdec": DOC_TYPE_RUC,
                "numdocdec": self.taxpayer_id,
                "ejercicio": period_start,
                "ejerciciofin": period_end,
                "accion": ACCION_QUERY,
            },
            timeout=90,
        )
        response.raise_for_status()
        return response.text
