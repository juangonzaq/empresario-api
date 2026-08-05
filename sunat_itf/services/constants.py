"""Endpoints and navigation for SUNAT's Consulta de ITF."""

# The SOL menu leaf that mints the signed report URL:
# Otras declaraciones y solicitudes > ITF > Consulta de ITF.
MENU_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
ITF_MENU_CODE = "13.6.1.1.1"

# Any request whose URL contains this marker carries the signed session params
# (p, tenc, prg, fecenv, usub) we need to POST the form.
REPORT_MARKER = "sci01Alias"
REPORT_URL = "https://ww1.sunat.gob.pe/cl-at-itconitf/sci01Alias"

# The form the report page posts back to itself.
DOC_TYPE_RUC = "06"
ACCION_QUERY = "ppsiguiente"
