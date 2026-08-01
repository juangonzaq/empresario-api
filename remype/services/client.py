"""Client for the REMYPE registry lookup (Ministerio de Trabajo).

The endpoint verifies a reCAPTCHA Enterprise token server-side and returns
``401 {"message": "Captcha invalido"}`` without one, so the lookup has to run inside a
real browser. The client keeps one browser open across lookups and mints a fresh token
per request — Enterprise tokens are single-use and short-lived.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from types import TracebackType
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .constants import (
    APP_URL,
    BASIC_AUTH_HEADER,
    BROWSER_ARGS,
    DEFAULT_TIMEOUT_MS,
    LOOKUP_PATH,
    NULL_DATE_MARKER,
    RECAPTCHA_ACTION,
    RECAPTCHA_SITE_KEY,
    STATUS_FOUND,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

# Runs in the page: mint a token, then post the lookup from the same origin.
LOOKUP_SCRIPT = """
async ([siteKey, action, ruc, path, auth]) => {
    const token = await new Promise((resolve, reject) => {
        grecaptcha.enterprise.ready(() => {
            grecaptcha.enterprise.execute(siteKey, {action: action})
                .then(resolve).catch(reject);
        });
    });
    const response = await fetch(path, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': auth,
            'tokenrecaptcha': token,
            'siteKey': siteKey,
        },
        body: JSON.stringify({ruc: ruc, respuest_new_catpchha: null, clientIp: ''}),
    });
    return {status: response.status, body: await response.text()};
}
"""


class RemypeLookupError(RuntimeError):
    """The REMYPE lookup could not be completed."""


def _clean(value: Any) -> str:
    """REMYPE pads every string with spaces."""
    return " ".join(str(value).split()).strip() if value is not None else ""


def _parse_date(value: Any) -> date | None:
    text = _clean(value)
    if not text or NULL_DATE_MARKER in text:
        return None
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None


@dataclass
class RemypeProfile:
    """A company's standing in the REMYPE registry."""

    ruc: str
    is_registered: bool = False
    business_name: str = ""
    condition: str = ""
    situation: str = ""
    mype_category: str = ""
    file_number: str = ""
    registry_code: int | None = None
    requested_on: date | None = None
    accredited_on: date | None = None
    deregistered_on: date | None = None
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Registered and not struck off."""
        return self.is_registered and self.deregistered_on is None

    @classmethod
    def not_registered(cls, ruc: str, message: str) -> "RemypeProfile":
        return cls(ruc=ruc, is_registered=False, message=_clean(message))

    @classmethod
    def from_row(cls, ruc: str, row: dict[str, Any]) -> "RemypeProfile":
        return cls(
            ruc=ruc,
            is_registered=True,
            business_name=_clean(row.get("RAZON_SOCIAL")),
            condition=_clean(row.get("CONDICION")),
            situation=_clean(row.get("SITUACIONEMPRESA")),
            mype_category=_clean(row.get("FLG_MYPE")),
            file_number=_clean(row.get("NUMEROFICHASOLICITUD")),
            registry_code=row.get("N_CODREG"),
            requested_on=_parse_date(row.get("FECHASOLICITUD")),
            accredited_on=_parse_date(row.get("FECHAACREDITACION")),
            deregistered_on=_parse_date(row.get("FECHABAJA")),
            payload=row,
        )


@dataclass
class RemypeClient:
    """Looks RUCs up in REMYPE.

    Use it as a context manager so one browser serves every lookup::

        with RemypeClient() as client:
            for ruc in rucs:
                profile = client.fetch(ruc)
    """

    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    _playwright: Any = field(default=None, init=False, repr=False)
    _browser: Any = field(default=None, init=False, repr=False)
    _page: Any = field(default=None, init=False, repr=False)

    def __enter__(self) -> "RemypeClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()

    def start(self) -> None:
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless, channel="chromium", args=BROWSER_ARGS
            )
            context = self._browser.new_context(
                user_agent=USER_AGENT, locale="es-PE",
                viewport={"width": 1440, "height": 900},
            )
            self._page = context.new_page()
            self._page.goto(APP_URL, wait_until="networkidle", timeout=self.timeout_ms)
            self._page.wait_for_function(
                "() => typeof grecaptcha !== 'undefined' && !!grecaptcha.enterprise",
                timeout=self.timeout_ms,
            )
        except PlaywrightError as exc:
            self.close()
            raise RemypeLookupError(f"Could not open the REMYPE page: {exc}") from exc

    def close(self) -> None:
        for resource, closer in ((self._browser, "close"), (self._playwright, "stop")):
            if resource is not None:
                try:
                    getattr(resource, closer)()
                except Exception:  # nothing useful to do while tearing down
                    logger.debug("Ignored error closing %s", resource, exc_info=True)
        self._playwright = self._browser = self._page = None

    def fetch(self, ruc: str) -> RemypeProfile:
        """Look one RUC up. Starts the browser on first use if needed."""
        if self._page is None:
            self.start()

        try:
            result = self._page.evaluate(
                LOOKUP_SCRIPT,
                [RECAPTCHA_SITE_KEY, RECAPTCHA_ACTION, ruc, LOOKUP_PATH,
                 BASIC_AUTH_HEADER],
            )
        except PlaywrightError as exc:
            raise RemypeLookupError(f"REMYPE lookup failed for {ruc}: {exc}") from exc

        return self._parse(ruc, result)

    def _parse(self, ruc: str, result: dict[str, Any]) -> RemypeProfile:
        import json

        if result["status"] != 200:
            raise RemypeLookupError(
                f"REMYPE returned HTTP {result['status']} for {ruc}: "
                f"{result['body'][:200]}"
            )
        try:
            envelope = json.loads(result["body"])
        except json.JSONDecodeError as exc:
            raise RemypeLookupError(f"REMYPE returned invalid JSON for {ruc}") from exc

        rows = envelope.get("data")
        if str(envelope.get("status")) != STATUS_FOUND or not rows:
            return RemypeProfile.not_registered(
                ruc, envelope.get("message") or "Not registered in REMYPE."
            )
        # The service returns a list but only ever one row per RUC.
        return RemypeProfile.from_row(ruc, rows[0])


def lookup(ruc: str) -> RemypeProfile:
    """Convenience one-shot lookup. Prefer the context manager for batches."""
    with RemypeClient() as client:
        return client.fetch(ruc)
