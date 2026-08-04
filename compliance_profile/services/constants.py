"""Endpoints and menu navigation for SUNAT's compliance profile."""

MENU_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
APP_ORIGIN = "https://ww1.sunat.gob.pe"

API_BASE = "https://api.sunat.gob.pe/v1/contribuyente/perfilcumplimiento"
# Any request whose URL contains this marker carries the bearer token we need.
API_MARKER = "api.sunat.gob.pe/v1/contribuyente/perfilcumplimiento"

# Menu options clicked in order after login, per SUNAT's own guide
# (Empresas > Perfil de Cumplimiento > Calificación del Perfil > Calificación
# Vigente). "Perfil de Cumplimiento" appears twice because the menu nests a
# subgroup with the same name inside the group.
MENU_PATH = (
    "Perfil de Cumplimiento",
    "Perfil de Cumplimiento",
    "Calificación del Perfil",
    "Calificación Vigente",
)
