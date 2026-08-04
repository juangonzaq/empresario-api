"""Scheduled scraping of the SUNAT compliance profile."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

from .services import (
    CompliancePortalClient,
    CompliancePortalError,
    ComplianceSynchronizer,
)

logger = logging.getLogger(__name__)


@shared_task(
    name="compliance_profile.scrape",
    autoretry_for=(CompliancePortalError,),
    retry_backoff=300,
    retry_kwargs={"max_retries": 2},
    # The login drives a real browser, so this task is slow and must not be
    # duplicated by a second worker picking it up mid-flight.
    time_limit=60 * 30,
    soft_time_limit=60 * 28,
)
def scrape_compliance_profile(refetch_details: bool = False) -> dict[str, Any]:
    """Pull the calificación history and each quarter's variables."""
    client = CompliancePortalClient(
        taxpayer_id=settings.SUNAT_RUC,
        username=settings.SUNAT_USER,
        password=settings.SUNAT_PASS,
    )
    client.login()

    result = ComplianceSynchronizer(client, refetch_details=refetch_details).run()

    logger.info("Compliance profile scrape finished: %s", result)
    return {
        "created": result.created,
        "updated": result.updated,
        "details_fetched": result.details_fetched,
        "details_failed": result.details_failed,
    }
