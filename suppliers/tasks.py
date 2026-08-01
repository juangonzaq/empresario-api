"""Scheduled checks of supplier standing on SUNAT."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from .models import Supplier
from .services import RucLookupError, SupplierMonitor

logger = logging.getLogger(__name__)


@shared_task(name="suppliers.check_all")
def check_all_suppliers(skip_checked_today: bool = True) -> dict[str, Any]:
    """Check every tracked supplier and record today's standing.

    ``skip_checked_today`` defaults to True so a retry after a partial failure only
    covers what is still missing instead of hammering SUNAT again.
    """
    result = SupplierMonitor().run(skip_checked_today=skip_checked_today)

    flagged = list(
        Supplier.objects.with_issues().values_list("ruc", flat=True)
    )
    if flagged:
        logger.warning("Suppliers needing attention: %s", ", ".join(flagged))

    return {
        "checked": result.checked,
        "changed": result.changed,
        "with_issues": result.with_issues,
        "failed": result.failed,
        "flagged": flagged,
    }


@shared_task(
    name="suppliers.check_one",
    autoretry_for=(RucLookupError,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
)
def check_supplier(ruc: str) -> dict[str, Any]:
    """Check a single supplier, retrying if SUNAT is unreachable."""
    supplier = Supplier.objects.get(ruc=ruc)
    check = SupplierMonitor().check(supplier)
    return {
        "ruc": ruc,
        "status": check.status,
        "condition": check.condition,
        "has_issue": check.has_issue,
        "changed": check.changed,
        "succeeded": check.succeeded,
    }
