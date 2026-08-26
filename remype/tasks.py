"""Scheduled REMYPE refresh.

REMYPE accreditation is granted once and rarely revoked, so this runs monthly rather
than daily, and skips any RUC whose stored check is still fresh.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from .services import RemypeSynchronizer
from .services.sync import DEFAULT_MAX_AGE_DAYS

logger = logging.getLogger(__name__)


def monitored_rucs() -> list[str]:
    """Every registered company plus every tracked supplier, de-duplicated."""
    from accounts.models import Organization
    from suppliers.models import Supplier

    rucs = list(Organization.objects.values_list("ruc", flat=True))
    rucs += list(Supplier.objects.tracked().values_list("ruc", flat=True))
    return list(dict.fromkeys(rucs))


@shared_task(name="remype.refresh")
def refresh_remype(
    rucs: list[str] | None = None, max_age_days: int | None = DEFAULT_MAX_AGE_DAYS
) -> dict[str, Any]:
    """Refresh REMYPE standing for the given RUCs (own company + suppliers by default)."""
    targets = rucs or monitored_rucs()
    if not targets:
        return {"checked": 0, "registered": 0, "changed": 0, "failed": 0, "skipped": 0}

    result = RemypeSynchronizer().run(targets, max_age_days=max_age_days)
    if result.changed:
        logger.warning("REMYPE standing changed for %s RUC(s)", result.changed)

    return {
        "checked": result.checked,
        "registered": result.registered,
        "changed": result.changed,
        "failed": result.failed,
        "skipped": result.skipped,
    }
