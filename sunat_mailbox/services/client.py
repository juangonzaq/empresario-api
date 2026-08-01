"""HTTP/browser client for the SUNAT electronic mailbox.

SUNAT's SOL login ends by generating a notification-viewer URL shaped like
``/ol-ti-itvisornoti/visor/master?hc=<hash>&token=<java-serialized UsuarioBean>``.
The ``hc`` hash is signed server-side by the menu application and cannot be
reproduced externally, so authentication requires a real browser.

Once the viewer is open, its cookies are handed over to a ``requests`` session and
every subsequent read goes through SUNAT's JSON endpoints, which is far faster and
more stable than driving the DOM.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .constants import (
    ATTEMPT_POLL_MS,
    BROWSER_ARGS,
    DEFAULT_TIMEOUT_MS,
    MENU_URL,
    USER_AGENT,
    VIEWER_BASE,
    VIEWER_MASTER_MARKER,
)

logger = logging.getLogger(__name__)


class SunatLoginError(RuntimeError):
    """Raised when the SOL login does not reach the mailbox viewer."""


@dataclass
class SunatMailboxClient:
    """Authenticates against SUNAT SOL and reads the electronic mailbox.

    Usage::

        client = SunatMailboxClient(taxpayer_id, username, password)
        client.login()
        for row in client.iter_messages(MessageType.NOTIFICATION):
            ...
    """

    taxpayer_id: str
    username: str
    password: str
    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    session: requests.Session = field(default_factory=requests.Session, init=False)
    viewer_url: str = field(default="", init=False)

    # ---------------------------------------------------------------- login
    def login(self) -> None:
        """Authenticate with a real browser, then hand the session to ``requests``."""
        viewer_urls: list[str] = []

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
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            page.on(
                "request",
                lambda req: viewer_urls.append(req.url)
                if VIEWER_MASTER_MARKER in req.url
                else None,
            )

            try:
                self._submit_credentials(page)
                self._await_viewer(page, viewer_urls)

                if not viewer_urls:
                    viewer_urls = [
                        frame.url for frame in page.frames
                        if VIEWER_MASTER_MARKER in frame.url
                    ]
                if not viewer_urls:
                    raise SunatLoginError(
                        "The mailbox viewer never loaded. Check the credentials, or "
                        "whether SUNAT is showing a captcha after failed attempts."
                    )

                self.viewer_url = viewer_urls[0]
                cookies = context.cookies()
            except PlaywrightError as exc:
                raise SunatLoginError(f"Browser automation failed: {exc}") from exc
            finally:
                browser.close()

        self.session = self._build_session(cookies)
        logger.info(
            "SOL login succeeded for %s (%d cookies carried over)",
            self.taxpayer_id, len(cookies),
        )

    def _submit_credentials(self, page: Any) -> None:
        page.goto(MENU_URL, wait_until="networkidle", timeout=self.timeout_ms)
        page.wait_for_selector("#txtRuc", state="visible", timeout=self.timeout_ms)
        page.click("#btnPorRuc")
        page.fill("#txtRuc", self.taxpayer_id)
        page.fill("#txtUsuario", self.username)
        page.fill("#txtContrasena", self.password)
        page.click("#btnAceptar")
        page.wait_for_url(lambda url: "loginMenuSol" not in url, timeout=self.timeout_ms)

    def _await_viewer(self, page: Any, viewer_urls: list[str]) -> None:
        """Wait for the viewer request. The menu injects it into an iframe via JS,
        so polling the network is more reliable than querying the DOM."""
        for _ in range(self.timeout_ms // ATTEMPT_POLL_MS):
            if viewer_urls:
                break
            page.wait_for_timeout(ATTEMPT_POLL_MS)
        page.wait_for_load_state("networkidle", timeout=self.timeout_ms)

    def _build_session(self, cookies: list[dict[str, Any]]) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "X-Ruc": self.taxpayer_id,
            "Referer": self.viewer_url,
        })
        for cookie in cookies:
            session.cookies.set(
                cookie["name"], cookie["value"],
                domain=cookie["domain"], path=cookie.get("path", "/"),
            )
        return session

    # ----------------------------------------------------------------- reads
    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.viewer_url:
            raise SunatLoginError("login() must be called before reading the mailbox.")
        response = self.session.get(f"{VIEWER_BASE}/{endpoint}", params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    def fetch_page(self, message_type: int, page: int = 1) -> dict[str, Any]:
        """Fetch one page of the listing: ``{total, records, rows: [...]}``."""
        return self._get("listNotiMenPag", {
            "tipoMsj": message_type,
            "codCarpeta": "00",
            "codEtiqueta": "",
            "page": page,
            "des_asunto": "",
            "codMensaje": "",
            "tipoOrden": "NADA",
        })

    def iter_messages(
        self, message_type: int, max_pages: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield every message row, following SUNAT's pagination to the end."""
        page, total_pages = 1, 1
        while page <= total_pages:
            payload = self.fetch_page(message_type, page=page)
            total_pages = payload.get("total") or 1
            if max_pages:
                total_pages = min(total_pages, max_pages)

            rows = payload.get("rows") or []
            logger.info(
                "message_type=%s page %s/%s -> %s rows",
                message_type, page, total_pages, len(rows),
            )
            yield from rows
            if not rows:
                break
            page += 1

    def fetch_detail(self, message_code: int, message_type: int) -> dict[str, Any]:
        """Fetch a message body along with its ``listAttach`` attachments."""
        return self._get("obtenerDetalleNotiMen", {
            "codigoMensaje": message_code,
            "tipoMsj": message_type,
        })

    def download_attachment(
        self, file_code: int, system_id: str = "0", annex: int = 0
    ) -> requests.Response:
        """Download one attachment.

        SUNAT returns ``200`` with an empty body unless the owning message's detail
        was requested earlier in the same session, so callers must call
        :meth:`fetch_detail` first. :class:`~.sync.MailboxSynchronizer` does this.
        """
        url = f"{VIEWER_BASE}/bajarArchivo/{file_code}/{annex}/{system_id}/{self.taxpayer_id}"
        response = self.session.get(
            url, timeout=120, headers={"Referer": self.viewer_url}
        )
        response.raise_for_status()
        return response
