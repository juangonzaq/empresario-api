"""Records REMYPE lookups against the database."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from ..models import RemypeCheck
from .client import RemypeClient, RemypeLookupError, RemypeProfile

logger = logging.getLogger(__name__)


# REMYPE accreditation rarely moves, so a stored check stays trustworthy for a long
# while. Lookups are expensive (a browser plus a reCAPTCHA round trip), so anything
# fresher than this is reused instead of re-fetched.
DEFAULT_MAX_AGE_DAYS = 30


@dataclass
class RemypeSyncResult:
    checked: int = 0
    registered: int = 0
    changed: int = 0
    failed: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.checked} checked, {self.registered} registered, "
            f"{self.changed} changed, {self.skipped} still fresh, {self.failed} failed"
        )


class RemypeSynchronizer:
    """Looks RUCs up in REMYPE and stores one check per RUC per day."""

    def __init__(self, client: RemypeClient | None = None):
        self.client = client

    def run(
        self,
        rucs: list[str],
        on_date: date | None = None,
        max_age_days: int | None = DEFAULT_MAX_AGE_DAYS,
    ) -> RemypeSyncResult:
        """Check every RUC, reusing a single browser for the whole batch.

        RUCs with a successful check newer than ``max_age_days`` are skipped; pass
        ``None`` to force a fresh lookup for all of them.
        """
        today = on_date or timezone.localdate()
        result = RemypeSyncResult()

        pending = [ruc for ruc in rucs if not self._is_fresh(ruc, today, max_age_days)]
        result.skipped = len(rucs) - len(pending)
        if not pending:
            return result

        # Every lookup happens first, then the results are persisted. Playwright's
        # sync API runs an event loop in this thread, and Django refuses ORM access
        # from an async context, so the two phases cannot be interleaved.
        for ruc, profile, error in self._fetch_all(pending):
            if profile is None:
                logger.warning("REMYPE lookup failed for %s: %s", ruc, error)
                result.failed += 1
                self._record_failure(ruc, today, error or "Unknown error")
                continue
            check = self._record(ruc, today, profile)
            result.checked += 1
            result.registered += check.is_registered
            result.changed += check.changed
        return result

    def _fetch_all(
        self, rucs: list[str]
    ) -> list[tuple[str, RemypeProfile | None, str | None]]:
        """Look every RUC up with a single browser session. Touches no models."""
        outcomes: list[tuple[str, RemypeProfile | None, str | None]] = []

        client = self.client
        owns_client = client is None
        if owns_client:
            # Only pay for the browser once we know there is work to do.
            client = RemypeClient()
        try:
            for ruc in rucs:
                try:
                    outcomes.append((ruc, client.fetch(ruc), None))
                except RemypeLookupError as exc:
                    outcomes.append((ruc, None, str(exc)))
        finally:
            if owns_client:
                client.close()
        return outcomes

    def _is_fresh(self, ruc: str, on_date: date, max_age_days: int | None) -> bool:
        if max_age_days is None:
            return False
        cutoff = on_date - timedelta(days=max_age_days)
        return RemypeCheck.objects.filter(
            ruc=ruc, succeeded=True, checked_on__gte=cutoff
        ).exists()

    @transaction.atomic
    def _record(self, ruc: str, on_date: date, profile: RemypeProfile) -> RemypeCheck:
        # Compare against the last successful check so a failed day in between does
        # not read as a change.
        previous = (
            RemypeCheck.objects.filter(ruc=ruc, succeeded=True)
            .exclude(checked_on=on_date)
            .order_by("-checked_on")
            .first()
        )
        changed = previous is not None and (
            previous.is_registered != profile.is_registered
            or previous.condition != profile.condition
        )

        check, _ = RemypeCheck.objects.update_or_create(
            ruc=ruc,
            checked_on=on_date,
            defaults={
                "is_registered": profile.is_registered,
                "business_name": profile.business_name,
                "condition": profile.condition,
                "situation": profile.situation,
                "mype_category": profile.mype_category,
                "file_number": profile.file_number,
                "registry_code": profile.registry_code,
                "requested_on": profile.requested_on,
                "accredited_on": profile.accredited_on,
                "deregistered_on": profile.deregistered_on,
                "changed": changed,
                "previous_condition": previous.condition if previous else "",
                "succeeded": True,
                "message": profile.message,
                "payload": profile.payload,
            },
        )
        if changed:
            logger.info(
                "REMYPE standing changed for %s: %r -> %r",
                ruc, check.previous_condition, profile.condition,
            )
        return check

    @transaction.atomic
    def _record_failure(self, ruc: str, on_date: date, error: str) -> RemypeCheck:
        check, _ = RemypeCheck.objects.update_or_create(
            ruc=ruc,
            checked_on=on_date,
            defaults={
                "is_registered": False, "changed": False, "succeeded": False,
                "message": error[:500], "payload": {},
            },
        )
        return check
