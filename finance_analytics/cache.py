"""Caché del panel financiero.

El overview cuesta caro: carga los comprobantes de la empresa y recalcula
series, clientes, ITF y el cruce de consistencia. Es exactamente lo que se
pide en cada carga de pantalla y en cada cambio de pestaña.

Los datos de origen solo cambian cuando termina una sincronización, así que se
cachean por empresa y se invalidan en los pocos puntos donde algo puede
moverse: fin de sincronización, cambio de estado de una alerta y generación
del briefing.

El TTL es un segundo cinturón: si alguna vía de invalidación se nos escapa, lo
peor que pasa es que el panel muestre datos de hace unos minutos, no que se
quede desactualizado para siempre.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

TTL_SECONDS = 300


def _generation(ruc: str) -> int:
    """Versión del caché de la empresa. El overview ahora se cachea por
    ventana (13 meses, 21, 81…), así que invalidar borrando UNA clave dejaría
    vivas las demás ventanas; subir la generación las vuelca todas a la vez
    sin necesitar `delete_pattern` (que no existe en todos los backends)."""
    return cache.get(f"finance:overview:gen:{ruc}", 0)


def overview_key(ruc: str, months: int = 13) -> str:
    return f"finance:overview:{ruc}:{_generation(ruc)}:{months}"


def get_overview(ruc: str, months: int = 13):
    return cache.get(overview_key(ruc, months))


def set_overview(ruc: str, payload, months: int = 13) -> None:
    cache.set(overview_key(ruc, months), payload, TTL_SECONDS)


def invalidate(ruc: str) -> None:
    """Se llama cuando algo que aparece en el panel ha cambiado."""
    key = f"finance:overview:gen:{ruc}"
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, None)
    logger.debug("Caché del panel invalidada para %s", ruc)
