"""Scheduled scraping of the SUNAT ITF report.

The beat schedule (see migration 0002) runs this on the 1st of every month to
capture the month that just closed: the year-to-date range then ends at the
previous month.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

from .services import ItfPortalClient, ItfPortalError, ItfSynchronizer
from .services.parsing import previous_period

logger = logging.getLogger(__name__)


@shared_task(
    name="sunat_itf.scrape",
    autoretry_for=(ItfPortalError,),
    retry_backoff=300,
    retry_kwargs={"max_retries": 2},
    # The login drives a real browser, so this task is slow and must not be
    # duplicated by a second worker picking it up mid-flight.
    time_limit=60 * 20,
    soft_time_limit=60 * 18,
)
def scrape_itf(period_end: str | None = None) -> dict[str, Any]:
    """Pull the ITF report. Defaults to the just-closed month (previous month)."""
    period_end = period_end or previous_period()
    client = ItfPortalClient(
        taxpayer_id=settings.SUNAT_RUC,
        username=settings.SUNAT_USER,
        password=settings.SUNAT_PASS,
    )
    client.login()

    result = ItfSynchronizer(client).run(period_end)
    logger.info("ITF scrape finished: %s", result)
    return {"stored": result.stored, "period_end": period_end}
