"""HTTP/browser client for SUNAT's compliance profile (perfil de cumplimiento).

The profile SPA at ww1.sunat.gob.pe talks to ``api.sunat.gob.pe`` with a
short-lived JWT (1 hour) that the SOL menu mints when the "Calificación
Vigente" option is opened. That minting is tied to the server-side SOL session,
so authentication requires a real browser: log in, walk the menu to the option,
and sniff the bearer token off the first API request the SPA makes.

Once the token is captured, every read is a plain ``requests`` call against the
JSON endpoints (``cabecera``, ``historico``, ``detalle``), which is far faster
and more stable than driving the DOM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from core.browser import browser_env

from sunat_mailbox.services.constants import (
    ATTEMPT_POLL_MS,
    BROWSER_ARGS,
    DEFAULT_TIMEOUT_MS,
    USER_AGENT,
)

from .constants import (
    API_BASE, API_MARKER, APP_ORIGIN, CAMPAIGN_OVERLAYS, CLICK_TIMEOUT_MS,
    MENU_PATH, MENU_URL,
)

logger = logging.getLogger(__name__)


class CompliancePortalError(RuntimeError):
    """Raised when the SOL login does not yield a compliance API token."""


class ComplianceLoginRejected(CompliancePortalError):
    """SOL no aceptó el usuario o la clave.

    Se distingue del resto de fallos porque tiene una consecuencia que ninguno
    de los demás debe provocar: marca la credencial de la empresa como
    inválida y corta el resto de la sincronización. Insistir con una clave que
    SUNAT acaba de rechazar puede bloquear el usuario SOL del cliente.

    El único momento en que se puede afirmar eso es cuando el portal nos deja
    en la pantalla de login. Que después se atragante un menú, cambie una
    opción o se cruce una campaña no dice nada sobre la clave —y tratarlo como
    si lo dijera le contaba al usuario que sus credenciales estaban mal
    mientras el problema era un pop-up—.
    """


@dataclass
class CompliancePortalClient:
    """Authenticates against SUNAT SOL and reads the compliance profile API.

    Usage::

        client = CompliancePortalClient(taxpayer_id, username, password)
        client.login()
        header = client.fetch_current()
        history = client.fetch_history()
        detail = client.fetch_detail(header["trimCal"])
    """

    taxpayer_id: str
    username: str
    password: str
    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    session: requests.Session = field(default_factory=requests.Session, init=False)
    token: str = field(default="", init=False)

    # ---------------------------------------------------------------- login
    def login(self) -> None:
        """Drive a real browser to the profile app and capture its bearer token."""
        tokens: list[str] = []

        def sniff(request: Any) -> None:
            if API_MARKER not in request.url:
                return
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                tokens.append(auth.split(" ", 1)[1])

        with sync_playwright() as playwright:
            # SUNAT's WAF resets the connection for Playwright's headless shell, so the
            # full Chromium build is required alongside the automation flag being hidden.
            browser = playwright.chromium.launch(
                headless=self.headless, channel="chromium", args=BROWSER_ARGS,
                env=browser_env(),
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale="es-PE",
                viewport={"width": 1440, "height": 900},
            )
            # The option may open in the menu iframe or a fresh tab; watch both.
            context.on("page", lambda page: page.on("request", sniff))
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            page.on("request", sniff)

            try:
                self._submit_credentials(page)
                self._open_compliance_app(page, tokens)
                self._await_token(page, tokens)
            except PlaywrightError as exc:
                raise CompliancePortalError(
                    f"Browser automation failed: {exc}"
                ) from exc
            finally:
                browser.close()

        if not tokens:
            raise CompliancePortalError(
                "The compliance app never called api.sunat.gob.pe, so no token was "
                "captured. Check the credentials, or run with --headful to watch "
                "whether SUNAT changed the menu or is showing a captcha."
            )

        self.token = tokens[0]
        self.session = self._build_session()
        logger.info("Compliance token captured for %s", self.taxpayer_id)

    def _submit_credentials(self, page: Any) -> None:
        page.goto(MENU_URL, wait_until="networkidle", timeout=self.timeout_ms)
        page.wait_for_selector("#txtRuc", state="visible", timeout=self.timeout_ms)
        page.click("#btnPorRuc")
        page.fill("#txtRuc", self.taxpayer_id)
        page.fill("#txtUsuario", self.username)
        page.fill("#txtContrasena", self.password)
        page.click("#btnAceptar")
        # La comprobación es la misma que en la Ficha RUC de SOL: no basta con
        # salir de la URL del login, hay que ver el menú. Con una clave mala
        # SOL redirige a una pantalla sin menú y el recorrido moría después
        # con un «Menu option was never shown» que culpaba a SUNAT.
        from ruc_profile.services.sol_ficha import SolLoginRejected, asegurar_menu_sol

        try:
            asegurar_menu_sol(page, self.timeout_ms)
        except SolLoginRejected as exc:
            raise ComplianceLoginRejected(str(exc)) from exc
        except (PlaywrightError, RuntimeError) as exc:
            raise CompliancePortalError(str(exc)) from exc

    @staticmethod
    def _login_error(page: Any) -> str:
        """Lo que SOL escribe en pantalla al rechazar, si lo escribe."""
        for selector in ("#spanMensaje", ".msgError", ".alert-danger"):
            try:
                elemento = page.locator(selector).first
                if elemento.is_visible():
                    texto = (elemento.inner_text() or "").strip()
                    if texto:
                        return texto[:300]
            except PlaywrightError:
                continue
        return ""

    @staticmethod
    def _dismiss_campaigns(page: Any) -> None:
        """Quita las campañas que SUNAT superpone al menú.

        Se llama antes de cada clic y no una sola vez: la campaña no está al
        terminar el login, aparece cuando le llega su respuesta, así que puede
        interponerse a mitad del recorrido del menú.
        """
        try:
            page.evaluate(
                """(selectores) => {
                    for (const selector of selectores) {
                        for (const nodo of document.querySelectorAll(selector)) {
                            nodo.remove();
                        }
                    }
                    document.body.classList.remove('modal-open');
                    document.body.style.overflow = '';
                }""",
                list(CAMPAIGN_OVERLAYS),
            )
        except PlaywrightError:
            # Limpiar es un apaño oportunista: si la página no deja evaluar
            # ahora mismo, el clic siguiente dirá si de verdad hacía falta.
            logger.debug("No se pudo limpiar la campaña de SUNAT", exc_info=True)

    def _open_compliance_app(self, page: Any, tokens: list[str]) -> None:
        """Walk the menu tree to "Calificación Vigente", which mints the token."""
        page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        for index, label in enumerate(MENU_PATH):
            if tokens:
                return  # the app is already open
            self._dismiss_campaigns(page)
            upcoming = MENU_PATH[index + 1] if index + 1 < len(MENU_PATH) else None
            if upcoming and upcoming != label and self._last_visible(page, upcoming):
                continue  # this level is already expanded
            element = self._last_visible(page, label)
            if element is None:
                # The menu renders lazily after login; give it one more beat.
                page.wait_for_timeout(ATTEMPT_POLL_MS * 4)
                element = self._last_visible(page, label)
            if element is None:
                raise CompliancePortalError(
                    f"Menu option '{label}' was never shown. SUNAT may have moved "
                    "the Perfil de Cumplimiento entry; run with --headful to check."
                )
            self._click_menu(page, element, label)
            page.wait_for_timeout(ATTEMPT_POLL_MS)

    def _click_menu(self, page: Any, element: Any, label: str) -> None:
        """Pulsa una opción del menú, aunque haya una campaña encima.

        Primero se intenta el clic de verdad, que es el que más se parece a lo
        que hace una persona. Si algo lo intercepta, se quita la capa y se
        pulsa el nodo por JavaScript: ese clic no viaja por el puntero, así que
        no hay capa que pueda comérselo. Quitar la campaña no bastaba —vuelve a
        mostrarse sola, y cada intento fallido costaba media espera entera—.
        """
        try:
            element.click(timeout=CLICK_TIMEOUT_MS)
            return
        except PlaywrightError:
            logger.info(
                "Clic en «%s» interceptado; se aparta la campaña y se pulsa "
                "el nodo directamente", label,
            )
        self._dismiss_campaigns(page)
        element.evaluate("nodo => nodo.click()")

    @staticmethod
    def _last_visible(page: Any, text: str) -> Any | None:
        """Deepest visible element with this exact text.

        The tree nests a subgroup named like its group, and expanded children are
        appended after their parent in the DOM, so the last visible match is the
        one that advances the navigation instead of collapsing it.
        """
        locator = page.get_by_text(text, exact=True)
        for index in range(locator.count() - 1, -1, -1):
            nth = locator.nth(index)
            try:
                if nth.is_visible():
                    return nth
            except PlaywrightError:
                continue
        return None

    def _await_token(self, page: Any, tokens: list[str]) -> None:
        for _ in range(self.timeout_ms // ATTEMPT_POLL_MS):
            if tokens:
                return
            page.wait_for_timeout(ATTEMPT_POLL_MS)

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": APP_ORIGIN,
            "Referer": f"{APP_ORIGIN}/",
            "Authorization": f"Bearer {self.token}",
        })
        return session

    # ----------------------------------------------------------------- reads
    def _get(self, path: str) -> Any | None:
        if not self.token:
            raise CompliancePortalError("login() must be called before reading.")
        response = self.session.get(f"{API_BASE}/{path}", timeout=60)
        if response.status_code == 204:
            return None
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def fetch_current(self) -> dict[str, Any] | None:
        """The vigente quarter's header: ``{numRuc, trimCal, codVarFinal, ...}``."""
        return self._get(f"cabecera/{self.taxpayer_id}")

    def fetch_history(self) -> list[dict[str, Any]]:
        """Every past quarter's header, newest first."""
        payload = self._get(f"historico/{self.taxpayer_id}") or {}
        return payload.get("cabecera") or []

    def fetch_detail(self, period: int | str) -> dict[str, Any] | None:
        """The variables behind one quarter's calificación (may be empty)."""
        return self._get(f"detalle/{period}/{self.taxpayer_id}")
