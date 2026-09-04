"""Maps the RHE portal query onto FeeReceipt rows."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.utils import timezone

from core import archive

from ..models import FeeReceipt
from .client import RhePortalClient
from .parsing import rows_to_fields

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    periods: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{self.created} recibos nuevos, {self.updated} actualizados "
            f"({', '.join(self.periods)})"
        )


def _previous(period: str) -> str:
    year, month = int(period[:4]), int(period[4:])
    if month == 1:
        return f"{year - 1}12"
    return f"{year}{month - 1:02d}"


class RheSynchronizer:
    """Pulls fee receipts per month and upserts them idempotently, keyed
    by (account, issuer, series, number) — re-running never duplicates."""

    def __init__(self, client: RhePortalClient):
        self.client = client

    def sync_periods(self, periods: list[str]) -> SyncResult:
        result = SyncResult(periods=list(periods))
        rows_by_period = self.client.collect(periods)
        for period in periods:
            self._store(rows_by_period.get(period, []), result)
        logger.info("RHE sync: %s", result)
        return result

    def backfill(
        self, start_period: str, stop_after_empty: int = 3,
        floor_period: str = "201701",
    ) -> SyncResult:
        """Initial load: walk backwards month by month until the history
        runs dry (N consecutive empty months) or the floor — January 2017,
        when the electronic fee receipt became mandatory. One SOL login
        serves the whole walk."""
        result = SyncResult()
        period, empty_streak = start_period, 0
        while period >= floor_period and empty_streak < stop_after_empty:
            rows = self.client.collect([period]).get(period, [])
            stored = self._store(rows, result)
            result.periods.append(period)
            empty_streak = 0 if stored else empty_streak + 1
            period = _previous(period)
        logger.info("RHE backfill: %s", result)
        return result

    def _store(self, rows: list[dict], result: SyncResult) -> int:
        stored = 0
        # The detail page is not a field: it is set aside before the mapping
        # and archived next to the row it came from.
        pages = [row.get("__detail_html__", b"") for row in rows]
        for fields, page in zip(rows_to_fields(rows, self.client.taxpayer_id), pages):
            if not fields["issuer_doc"] or not fields["number"]:
                logger.warning("RHE row without key fields: %r", fields["raw"])
                continue
            receipt, created = FeeReceipt.objects.update_or_create(
                account_ruc=fields["account_ruc"],
                issuer_doc=fields["issuer_doc"],
                series=fields["series"],
                number=fields["number"],
                defaults={**fields, "last_seen_at": timezone.now()},
            )
            if page:
                self._archive_page(receipt, page)
            stored += 1
            if created:
                result.created += 1
            else:
                result.updated += 1
        return stored

    @staticmethod
    def _archive_page(receipt: FeeReceipt, page: bytes) -> None:
        """The detail page is the only «document» SUNAT hands the paying
        company, so it is the receipt's file — refreshed on every sync,
        because the payments list keeps changing after issue. A PDF the
        worker handed over (manual registration) is the better copy and
        is never replaced by the page."""
        if receipt.file and receipt.file.name.lower().endswith(".pdf"):
            return
        archive.store(receipt.file, page, "html")
        receipt.save(update_fields=["file", "updated_at"])
