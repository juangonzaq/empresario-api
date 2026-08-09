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
def check_supplier(ruc: str, account_ruc: str | None = None) -> dict[str, Any]:
    """Check one supplier RUC, retrying if SUNAT is unreachable.

    El RUC dejó de identificar una sola fila: el mismo proveedor puede estar
    en la cartera de varias empresas. Se consulta a SUNAT una vez y el
    resultado se anota en todas las fichas que lo tengan (o solo en la de
    ``account_ruc``, si se indica).
    """
    suppliers = Supplier.objects.filter(ruc=ruc)
    if account_ruc:
        suppliers = suppliers.filter(account_ruc=account_ruc)

    monitor = SupplierMonitor()
    cache: dict = {}
    checks = [monitor.check(s, cache=cache) for s in suppliers]
    if not checks:
        return {"ruc": ruc, "succeeded": False, "error": "Proveedor no registrado."}

    latest = checks[0]
    return {
        "ruc": ruc,
        "fichas": len(checks),
        "status": latest.status,
        "condition": latest.condition,
        "has_issue": latest.has_issue,
        "changed": any(c.changed for c in checks),
        "succeeded": all(c.succeeded for c in checks),
    }
