"""Maps SUNAT compliance payloads onto the local models."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from ..models import ComplianceRating, ComplianceVariable
from .client import CompliancePortalClient
from .parsing import header_fields, iter_detail_variables, parse_int

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Outcome of a synchronisation run."""

    created: int = 0
    updated: int = 0
    details_fetched: int = 0
    details_failed: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated

    def __str__(self) -> str:
        summary = f"{self.created} created, {self.updated} updated"
        if self.details_fetched or self.details_failed:
            summary += (
                f"; details: {self.details_fetched} fetched, "
                f"{self.details_failed} failed"
            )
        return summary


class ComplianceSynchronizer:
    """Pulls the calificación history from SUNAT and upserts it locally.

    ``fetch_details`` also pulls each quarter's variables. Historical quarters
    are immutable so their detail is fetched once; the vigente quarter is
    refreshed on every run because SUNAT keeps loading incumplimientos into it
    monthly. ``refetch_details`` forces every quarter through again.
    """

    def __init__(
        self,
        client: CompliancePortalClient,
        *,
        fetch_details: bool = True,
        refetch_details: bool = False,
    ):
        self.client = client
        self.fetch_details = fetch_details or refetch_details
        self.refetch_details = refetch_details

    def run(self) -> SyncResult:
        result = SyncResult()
        current = self.client.fetch_current()
        history = self.client.fetch_history()

        rows: dict[int, dict[str, Any]] = {}
        for row in history:
            period = parse_int(row.get("trimCal"))
            if period:
                rows[period] = row
        current_period = parse_int(current.get("trimCal")) if current else None
        if current_period:
            rows[current_period] = current

        ratings: list[ComplianceRating] = []
        with transaction.atomic():
            for period, row in sorted(rows.items()):
                rating, created = ComplianceRating.objects.update_or_create(
                    taxpayer_id=self.client.taxpayer_id,
                    period=period,
                    defaults={
                        **header_fields(row),
                        "is_current": period == current_period,
                    },
                )
                ratings.append(rating)
                result.created += created
                result.updated += not created
            # A quarter absent from this run's payloads must still lose the flag.
            ComplianceRating.objects.for_taxpayer(self.client.taxpayer_id).exclude(
                period=current_period or 0
            ).filter(is_current=True).update(is_current=False)

        if self.fetch_details:
            for rating in ratings:
                if (
                    rating.detail_payload is not None
                    and not rating.is_current
                    and not self.refetch_details
                ):
                    continue
                try:
                    self._sync_detail(rating, result)
                except Exception:
                    # One broken quarter must not abort the whole run.
                    result.details_failed += 1
                    logger.exception(
                        "Detail fetch failed for %s period %s",
                        rating.taxpayer_id, rating.period,
                    )
        return result

    def _sync_detail(self, rating: ComplianceRating, result: SyncResult) -> None:
        detail = self.client.fetch_detail(rating.period)
        variables = list(iter_detail_variables(detail))
        with transaction.atomic():
            rating.detail_payload = detail
            rating.detail_fetched_at = timezone.now()
            rating.save(
                update_fields=["detail_payload", "detail_fetched_at", "updated_at"]
            )
            # Replaced wholesale, like mailbox attachments: SUNAT repeats variable
            # codes, so there is no stable key to upsert on.
            rating.variables.all().delete()
            ComplianceVariable.objects.bulk_create(
                ComplianceVariable(rating=rating, **fields) for fields in variables
            )
        result.details_fetched += 1
        logger.info(
            "Period %s: %s variables stored", rating.period, len(variables)
        )
