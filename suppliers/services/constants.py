"""Endpoints and literals for SUNAT's public RUC lookup."""

BASE_URL = "https://e-consultaruc.sunat.gob.pe/cl-ti-itmrconsruc"
LOOKUP_URL = f"{BASE_URL}/jcrS00Alias"

ACTION_BY_RUC = "consPorRuc"
ACTION_BY_NAME = "consPorRazonSoc"
ACTION_BY_DOCUMENT = "consPorTipdoc"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

# Values SUNAT returns when a taxpayer is in good standing. Anything else is
# treated as an issue, so unknown future values fail safe (flagged, not ignored).
STATUS_ACTIVE = "ACTIVO"
CONDITION_FOUND = "HABIDO"

# Field labels on the results page, mapped to model field names.
FIELD_LABELS = {
    "Tipo Contribuyente": "taxpayer_type",
    "Nombre Comercial": "trade_name",
    "Fecha de Inscripción": "registered_on",
    "Fecha Inicio de Actividades": "started_activities_on",
    "Estado": "status",
    "Condición": "condition",
    "Domicilio Fiscal": "fiscal_address",
    "Actividad(es) Económica(s)": "economic_activities",
    "Sistema de Emisión Electrónica": "electronic_invoicing",
    "Padrones": "registries",
}

INVALID_RUC_MARKER = "no es válido"
