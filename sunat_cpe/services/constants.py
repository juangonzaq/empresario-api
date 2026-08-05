"""Endpoints, navigation and query-type map for Consultar Factura y Nota."""

from ..models import DocumentClass, Direction

# SOL menu leaf: Comprobantes de pago > SEE-SOL > Factura Electrónica >
# Consultar Factura y Nota.
MENU_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
CPE_MENU_CODE = "11.5.3.1.2"

# Any request whose URL contains this marker carries the session that authorizes
# the consultar.do calls (cookie ITCONSCPEMYPESEESESSION).
APP_MARKER = "itconscpemype"
APP_BASE = "https://ww1.sunat.gob.pe/ol-ti-itconscpemype"
CONSULTAR_URL = f"{APP_BASE}/consultar.do"

ACTION_QUERY = "realizarConsulta"
ACTION_DOWNLOAD_XML = "descargarFactura"
ESTADO_VALIDO = "1"

# tipoConsulta -> (document class, direction, is_rejected view).
# 12 (FE rechazadas) is a factura view filtered to rejected ones.
TIPO_CONSULTA = {
    "10": (DocumentClass.INVOICE, Direction.ISSUED, False),
    "11": (DocumentClass.INVOICE, Direction.RECEIVED, False),
    "12": (DocumentClass.INVOICE, Direction.RECEIVED, True),
    "13": (DocumentClass.CREDIT_NOTE, Direction.ISSUED, False),
    "14": (DocumentClass.CREDIT_NOTE, Direction.RECEIVED, False),
    "15": (DocumentClass.DEBIT_NOTE, Direction.ISSUED, False),
    "16": (DocumentClass.DEBIT_NOTE, Direction.RECEIVED, False),
}
ALL_TIPOS = tuple(TIPO_CONSULTA)

# codCpe -> document class, used to classify a record independently of the query.
COD_CPE_CLASS = {
    "01": DocumentClass.INVOICE,
    "07": DocumentClass.CREDIT_NOTE,
    "08": DocumentClass.DEBIT_NOTE,
}
