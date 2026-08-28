"""Scheduled capture of the full SUNAT RUC profile.

The full profile is ten HTTP requests per RUC, and most of it (historical data,
representatives, headcounts) moves slowly, so this runs monthly rather than daily.
The lighter ``suppliers.check_all`` still watches estado/condición every day.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task

from .models import RucSnapshot
from .services import RucProfileSynchronizer
from .services.sync import DEFAULT_MAX_AGE_DAYS

logger = logging.getLogger(__name__)


# Un emisor que te facturó hace más de un año ya no pesa en una fiscalización
# en curso; no vale la pena gastar consultas a SUNAT en él.
RECENT_ISSUER_MONTHS = 12


def recent_issuer_rucs(months: int = RECENT_ISSUER_MONTHS) -> list[str]:
    """RUCs que emitieron facturas a alguna empresa en los últimos meses.

    La fiscalización de proveedores mira a **todo** emisor, esté o no en la
    cartera; las señales de personal y locales solo existen si su ficha se
    capturó. Sin esto, el ~95 % de los emisores no tendría ficha.
    """
    from django.utils import timezone

    from sunat_cpe.models import DocumentClass, ElectronicInvoice

    since = timezone.localdate() - timedelta(days=months * 31)
    return list(
        ElectronicInvoice.objects.received()
        .filter(
            is_cancelled=False, issue_date__gte=since,
            document_class__in=[DocumentClass.INVOICE, DocumentClass.DEBIT_NOTE],
        )
        .exclude(issuer_ruc="")
        .values_list("issuer_ruc", flat=True)
        .distinct()
    )


def profiled_rucs() -> list[str]:
    """Every registered company, tracked supplier and recent issuer, de-duplicated."""
    from accounts.models import Organization
    from suppliers.models import Supplier

    rucs = list(Organization.objects.values_list("ruc", flat=True))
    rucs += list(Supplier.objects.tracked().values_list("ruc", flat=True))
    rucs += recent_issuer_rucs()
    return list(dict.fromkeys(r for r in rucs if r))


@shared_task(name="ruc_profile.capture")
def capture_ruc_profiles(
    rucs: list[str] | None = None, max_age_days: int | None = DEFAULT_MAX_AGE_DAYS
) -> dict[str, Any]:
    """Capture the full profile for the given RUCs (own company + suppliers by default)."""
    targets = rucs or profiled_rucs()
    if not targets:
        return {"captured": 0, "changed": 0, "with_risk": 0, "failed": 0, "skipped": 0}

    result = RucProfileSynchronizer().run(targets, max_age_days=max_age_days)

    flagged = list(
        RucSnapshot.objects.filter(ruc__in=targets)
        .latest_per_ruc().with_risk().values_list("ruc", flat=True)
    )
    if flagged:
        logger.warning("RUCs with risk signals: %s", ", ".join(flagged))

    return {
        "captured": result.captured,
        "changed": result.changed,
        "with_risk": result.with_risk,
        "failed": result.failed,
        "skipped": result.skipped,
        "flagged": flagged,
    }
