"""Background recalculation.

The engine must not run on every request. It runs here: on a source-data change
(enqueue ``recalculate_company`` from the changing app inside
``transaction.on_commit`` so a rolled-back change never triggers an evaluation),
or on a periodic sweep scheduled in the DB (django_celery_beat).
"""

from __future__ import annotations

import logging

from celery import shared_task

from .services.engine import evaluate_company_by_ruc
from .services.snapshots import create_compliance_snapshot

logger = logging.getLogger(__name__)


@shared_task(name="obligations.recalculate_company", time_limit=300, soft_time_limit=270)
def recalculate_company(ruc: str) -> dict | None:
    result = evaluate_company_by_ruc(ruc)
    if result is not None:
        create_compliance_snapshot(ruc)
    return result


@shared_task(name="obligations.snapshot_all", time_limit=60 * 30, soft_time_limit=60 * 28)
def snapshot_all_companies() -> dict:
    """Evaluate and snapshot every company. Meant for a daily beat schedule so
    the trend chart has a point per day even when nobody opens the screen."""
    from accounts.models import Organization

    done = 0
    for ruc in Organization.objects.values_list("ruc", flat=True):
        try:
            if evaluate_company_by_ruc(ruc) is not None:
                create_compliance_snapshot(ruc)
                done += 1
        except Exception:  # pragma: no cover - one company must not stop the sweep
            logger.exception("compliance snapshot failed for %s", ruc)
    return {"companies": done}
