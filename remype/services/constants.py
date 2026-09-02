"""Endpoints and literals for the Ministry of Labour's REMYPE lookup."""

APP_URL = "https://apps.trabajo.gob.pe/consultas-remype/app/index.html"
LOOKUP_PATH = "../consulta/remype.tra"

# Site key de reCAPTCHA v3 (el portal migró de Enterprise a v3 en 2026-08,
# rotando la clave). Es solo el RESPALDO: el cliente lee la clave vigente del
# propio <script> de la página en cada arranque, porque el backend rechaza los
# tokens de una clave vieja como «Captcha invalido».
RECAPTCHA_SITE_KEY = "6LdpOZktAAAAANlPMrHhM6uDOcrM2DxKc0TfVUiv"
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
