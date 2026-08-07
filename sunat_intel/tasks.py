"""Background processing: analyze new messages and rebuild cases.

Chain this after ``sunat_mailbox.scrape`` so every new message is analyzed
without blocking any request. Results are cached by fingerprint, so re-runs
only pay for what actually changed.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

from .services import analyzer, cases

logger = logging.getLogger(__name__)


@shared_task(
    name="sunat_intel.analyze_mailbox",
    time_limit=60 * 60,
    soft_time_limit=58 * 60,
)
def analyze_mailbox(limit: int | None = None, force: bool = False) -> dict[str, Any]:
    stats = analyzer.analyze_pending(limit=limit, force=force)
    case_stats = cases.rebuild_cases(settings.SUNAT_RUC)
    logger.info("Mailbox analysis finished: %s / cases: %s", stats, case_stats)
    return {**stats, "cases": case_stats}
