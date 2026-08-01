"""Endpoints and browser settings for SUNAT's SOL menu and notification viewer."""

MENU_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm?exe=buzon"
VIEWER_DOMAIN = "https://ww1.sunat.gob.pe"
VIEWER_BASE = f"{VIEWER_DOMAIN}/ol-ti-itvisornoti/visor"
VIEWER_MASTER_MARKER = "/visor/master"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# Hides the navigator.webdriver flag; without it SUNAT's WAF resets the connection.
BROWSER_ARGS = ["--disable-blink-features=AutomationControlled"]

DEFAULT_TIMEOUT_MS = 90_000
ATTEMPT_POLL_MS = 500
