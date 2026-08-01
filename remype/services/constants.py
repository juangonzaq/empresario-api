"""Endpoints and literals for the Ministry of Labour's REMYPE lookup."""

APP_URL = "https://apps.trabajo.gob.pe/consultas-remype/app/index.html"
LOOKUP_PATH = "../consulta/remype.tra"

# reCAPTCHA Enterprise site key, read from the page's own controller. The backend
# rejects any request without a token it can verify, so a real browser is required.
RECAPTCHA_SITE_KEY = "6Le-nQksAAAAAH-QyP3vCcI-05KTagQoh7auXaPi"
RECAPTCHA_ACTION = "remype"

# The Angular app sets this on every request; the endpoint 401s without it.
BASIC_AUTH_HEADER = "Basic YWRtaW46YWRtaW4="

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
BROWSER_ARGS = ["--disable-blink-features=AutomationControlled"]
DEFAULT_TIMEOUT_MS = 90_000

# Response envelope: status "0" carries data, "1" means the RUC is not registered.
STATUS_FOUND = "0"
STATUS_NOT_FOUND = "1"

# The service pads values with spaces and uses this literal for absent dates.
NULL_DATE_MARKER = "---"
