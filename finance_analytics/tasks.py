"""Background refresh: XML extraction + alert regeneration.

Chain after the CPE/ITF scrapers so analytics stay current without blocking
any request. Both steps are idempotent and fingerprint-cached.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from .services.alerts import rebuild_alerts
from .services.xml_extract import extract_pending

logger = logging.getLogger(__name__)


@shared_task(name="finance_analytics.refresh", time_limit=60 * 15)
def refresh_finance(force: bool = False) -> dict[str, Any]:
    stats = extract_pending(force=force)
    alerts = rebuild_alerts()
    logger.info("Finance refresh: %s / alerts: %s", stats, alerts)
    return {**stats, "alerts": alerts}
