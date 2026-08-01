"""Scheduled scraping of the SUNAT electronic mailbox."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

from .services import MailboxSynchronizer, SunatLoginError, SunatMailboxClient

logger = logging.getLogger(__name__)


@shared_task(
    name="sunat_mailbox.scrape",
    autoretry_for=(SunatLoginError,),
    retry_backoff=300,
    retry_kwargs={"max_retries": 2},
    # The login drives a real browser, so this task is slow and must not be
    # duplicated by a second worker picking it up mid-flight.
    time_limit=60 * 30,
    soft_time_limit=60 * 28,
)
def scrape_mailbox(
    download_attachments: bool = True, max_pages: int | None = None
) -> dict[str, Any]:
    """Pull new mailbox messages and, by default, their attachment text."""
    client = SunatMailboxClient(
        taxpayer_id=settings.SUNAT_RUC,
        username=settings.SUNAT_USER,
        password=settings.SUNAT_PASS,
    )
    client.login()

    synchronizer = MailboxSynchronizer(
        client, download_attachments=download_attachments
    )
    result = synchronizer.run(max_pages=max_pages)

    logger.info("Mailbox scrape finished: %s", result)
    return {
        "created": result.created,
        "updated": result.updated,
        "failed": result.failed,
        "attachments_downloaded": result.attachments_downloaded,
        "attachments_failed": result.attachments_failed,
    }
