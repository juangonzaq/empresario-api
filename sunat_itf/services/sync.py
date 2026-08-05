"""Maps the Consulta de ITF HTML onto ItfRecord rows."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction

from ..models import ItfRecord
from .client import ItfPortalClient
from .parsing import iter_records, ytd_range

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    stored: int = 0
    periods: tuple[str, str] = ("", "")

    def __str__(self) -> str:
        return (
            f"{self.stored} ITF records stored for "
            f"{self.periods[0]}–{self.periods[1]}"
        )


class ItfSynchronizer:
    """Pulls the year-to-date ITF report and replaces the local snapshot.

    The report is a full snapshot of the requested range, and a single ITF row
    (same bank, period, operation code, amount) can legitimately repeat, so there
    is no stable per-row key to upsert on. The whole range is therefore replaced
    wholesale for the taxpayer on each run, which keeps re-runs idempotent.
    """

    def __init__(self, client: ItfPortalClient):
        self.client = client

    def run(self, period_end: str) -> SyncResult:
        period_start, period_end = ytd_range(period_end)
        html = self.client.fetch_report(period_start, period_end)
        records = list(iter_records(html, self.client.taxpayer_id))

        periods_in_range = {
            f"{y}{m:02d}"
            for y in [int(period_start[:4])]
            for m in range(int(period_start[4:]), int(period_end[4:]) + 1)
        }

        with transaction.atomic():
            deleted, _ = (
                ItfRecord.objects.for_taxpayer(self.client.taxpayer_id)
                .filter(period__in=periods_in_range)
                .delete()
            )
            ItfRecord.objects.bulk_create(
                ItfRecord(**fields) for fields in records
            )

        logger.info(
            "ITF %s–%s: %d rows stored (%d replaced)",
            period_start, period_end, len(records), deleted,
        )
        return SyncResult(stored=len(records), periods=(period_start, period_end))
