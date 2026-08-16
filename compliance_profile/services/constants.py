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

# SUNAT superpone campañas al menú recién cargado —«actualiza tus datos del
# RUC» y similares—: un div a pantalla completa con un iframe dentro. El menú
# de abajo sigue funcionando, pero la capa se come los clics, así que
# Playwright reintenta treinta segundos contra una opción que ve, considera
# estable, y no puede pulsar. Se quitan del DOM antes de navegar.
#
# Se quitan, no se cierran: el botón de cerrar vive dentro del iframe de la
# campaña, cambia con cada campaña, y pulsarlo sería interactuar con un
# formulario de modificación de datos del RUC. Este scraper solo lee.
CAMPAIGN_OVERLAYS = (
    "#divModalCampana",
    ".modal-backdrop",
)

# Cuánto se espera a que una opción del menú admita un clic normal antes de
# pulsarla por JavaScript. Corto a propósito: si algo la tapa, esperar más no
# lo destapa —la campaña se queda—, y con el tiempo por defecto (90 s) cada
# opción tapada se comía el paso entero. Observado contra el portal: la campaña
# intercepta las cuatro opciones del recorrido, así que esto se paga cuatro
# veces por sincronización.
CLICK_TIMEOUT_MS = 5_000
