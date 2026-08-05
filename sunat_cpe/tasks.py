"""Scheduled scraping of electronic comprobantes (all types) and their XML."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

from .services import CpePortalClient, CpePortalError, CpeSynchronizer
from .services.parsing import recent_periods

logger = logging.getLogger(__name__)


@shared_task(
    name="sunat_cpe.scrape_daily",
    autoretry_for=(CpePortalError,),
    retry_backoff=300,
    retry_kwargs={"max_retries": 2},
    time_limit=60 * 40,
    soft_time_limit=60 * 38,
)
def scrape_cpe_daily(window_months: int = 2, download_xml: bool = True) -> dict[str, Any]:
    """Daily cross-check: re-query the recent months across all comprobante types.

    New documents (e.g. a nota de crédito that offsets a factura) are picked up as
    their own records referencing the original, so balances stay correct without
    re-validating each document.
    """
    client = CpePortalClient(
        taxpayer_id=settings.SUNAT_RUC,
        username=settings.SUNAT_USER,
        password=settings.SUNAT_PASS,
    )
    client.login()
    result = CpeSynchronizer(client, download_xml=download_xml).sync_periods(
        recent_periods(window_months)
    )
    logger.info("CPE daily scrape finished: %s", result)
    return {
        "created": result.created,
        "updated": result.updated,
        "xml_downloaded": result.xml_downloaded,
        "xml_failed": result.xml_failed,
    }


@shared_task(
    name="sunat_cpe.backfill",
    autoretry_for=(CpePortalError,),
    retry_backoff=300,
    retry_kwargs={"max_retries": 2},
    time_limit=60 * 110,
    soft_time_limit=60 * 100,
)
def backfill_cpe(
    start_period: str | None = None, stop_after_empty: int = 3
) -> dict[str, Any]:
    """One-off historical load: walk backwards until the data runs out."""
    from .services.parsing import current_period

    client = CpePortalClient(
        taxpayer_id=settings.SUNAT_RUC,
        username=settings.SUNAT_USER,
        password=settings.SUNAT_PASS,
    )
    client.login()
    result = CpeSynchronizer(client).backfill(
        start_period or current_period(), stop_after_empty=stop_after_empty
    )
    logger.info("CPE backfill finished: %s", result)
    return {
        "created": result.created,
        "updated": result.updated,
        "oldest_period": result.oldest_period,
        "xml_downloaded": result.xml_downloaded,
    }
