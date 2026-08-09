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


def overview_key(ruc: str) -> str:
    return f"finance:overview:{ruc}"


def get_overview(ruc: str):
    return cache.get(overview_key(ruc))


def set_overview(ruc: str, payload) -> None:
    cache.set(overview_key(ruc), payload, TTL_SECONDS)


def invalidate(ruc: str) -> None:
    """Se llama cuando algo que aparece en el panel ha cambiado."""
    cache.delete(overview_key(ruc))
    logger.debug("Caché del panel invalidada para %s", ruc)
