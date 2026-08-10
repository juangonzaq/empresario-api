"""Background processing: analyze new messages and rebuild cases.

Runs right after the mailbox is scraped, so every new message is analyzed
without blocking any request. Results are cached by fingerprint, so re-runs
only pay for what actually changed.

El RUC es obligatorio. Antes salía de ``settings.SUNAT_RUC`` —el de una sola
empresa, vacío por defecto—, lo que en una instalación con varias empresas
reconstruía los casos de la que estuviera en el ``.env`` en lugar de los de la
que se acababa de sincronizar.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from .services import analyzer, cases

logger = logging.getLogger(__name__)


@shared_task(
    name="sunat_intel.analyze_mailbox",
    time_limit=60 * 60,
    soft_time_limit=58 * 60,
)
def analyze_mailbox(
    taxpayer_id: str,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    stats = analyzer.analyze_pending(
        taxpayer_id=taxpayer_id, limit=limit, force=force
    )
    case_stats = cases.rebuild_cases(taxpayer_id)
    logger.info(
        "Mailbox analysis finished for %s: %s / cases: %s",
        taxpayer_id, stats, case_stats,
    )
    return {**stats, "cases": case_stats}
