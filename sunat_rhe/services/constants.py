"""Endpoints and navigation for SUNAT's received fee receipts query.

Captured from a live session (menu option «Consulta de recibos» of the
Recibo por Honorarios Electrónico system, where the company is the USER
of the service):

- Menu leaf 11.5.1.1.14 fires ``cpelec001Alias?accion=LlamaCriterioConsulatRec1``
  on the ``ol-ti-itreciboelectronico`` application, which mints the
  ``IARECIBOELECTRONICOSESSION`` cookie.
- The criteria form posts back to the same alias with
  ``accion=CapturaCriterioConsulatRec1`` and a date range.
"""

MENU_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
RHE_MENU_CODE = "11.5.1.1.14"

# Any request whose URL contains this marker carries the app's signed
# session; its cookies authorize the direct POST below.
APP_MARKER = "itreciboelectronico"
APP_URL = "https://ww1.sunat.gob.pe/ol-ti-itreciboelectronico/cpelec001Alias"

ACTION_QUERY = "CapturaCriterioConsulatRec1"
# One receipt's detail, by position in the LAST listing (stateful).
ACTION_DETAIL = "CapturaCriterioConsultaRec2"

# tipocomprobante: 01 recibo, 02 nota de crédito, 03 nota de débito —
# everything; tipoestado 00 = all states (reverted ones come flagged).
QUERY_BASE = {
    "accion": ACTION_QUERY,
    "tipocomprobante": "01;02;03;",
    "ruc_emisor": "",
    "num_serie": "",
    "num_comprob": "",
    "tipoestado": "00",
}
QUERY_TYPES = ["01", "02", "03"]

# A results table qualifies when its header mentions any of these.
TABLE_HEADER_WORDS = ("recibo", "serie", "emisor", "honorario", "comprobante")
