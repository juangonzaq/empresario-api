"""Scheduled scraping of the SUNAFIL casilla."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

from .models import SunafilItem
from .services import SunafilClient, SunafilLoginError, SunafilSynchronizer

logger = logging.getLogger(__name__)


@shared_task(
    name="sunafil.scrape",
    autoretry_for=(SunafilLoginError,),
    retry_backoff=300,
    retry_kwargs={"max_retries": 2},
)
def scrape_sunafil(fetch_details: bool = True) -> dict[str, Any]:
    """Pull the casilla listings daily.

    Requirements and notifications carry response deadlines, so this runs daily even
    though orientations alone would not need it.
    """
    if not all((settings.SUNAT_RUC, settings.SUNAT_USER, settings.SUNAT_PASS)):
        return {"created": 0, "updated": 0, "details_fetched": 0, "failed": 0}

    client = SunafilClient(
        taxpayer_id=settings.SUNAT_RUC,
        username=settings.SUNAT_USER,
        password=settings.SUNAT_PASS,
    )
    client.login()

    result = SunafilSynchronizer(client, fetch_details=fetch_details).run()

    pending = list(
        SunafilItem.objects.filter(taxpayer_id=settings.SUNAT_RUC)
        .actionable().unread()
        .values_list("record_number", flat=True)
    )
    if pending:
        logger.warning("SUNAFIL obligations unread: %s", ", ".join(pending))

    return {
        "created": result.created,
        "updated": result.updated,
        "details_fetched": result.details_fetched,
        "failed": result.failed,
        "pending": pending,
    }
