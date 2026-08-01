"""Runs the daily SUNAT check over the registered suppliers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils import timezone

from ..models import Supplier, SupplierCheck
from .ruc_client import RucLookupClient, RucLookupError, TaxpayerProfile

logger = logging.getLogger(__name__)

# Profile fields copied onto the Supplier row on every successful check.
MIRRORED_FIELDS = (
    "business_name", "trade_name", "taxpayer_type", "fiscal_address",
    "economic_activities", "registered_on", "started_activities_on",
)


@dataclass
class MonitorResult:
    """Outcome of a monitoring run."""

    checked: int = 0
    failed: int = 0
    changed: int = 0
    with_issues: int = 0

    def __str__(self) -> str:
        return (
            f"{self.checked} checked, {self.changed} changed, "
            f"{self.with_issues} with issues, {self.failed} failed"
        )


class SupplierMonitor:
    """Looks every tracked supplier up on SUNAT and records the result.

    One :class:`~suppliers.models.SupplierCheck` is stored per supplier per day, so
    re-running on the same day updates that day's row instead of duplicating it.
    """

    def __init__(self, client: RucLookupClient | None = None):
        self.client = client or RucLookupClient()

    def run(
        self,
        suppliers=None,
        on_date: date | None = None,
        skip_checked_today: bool = False,
    ) -> MonitorResult:
        queryset = Supplier.objects.tracked() if suppliers is None else suppliers
        today = on_date or timezone.localdate()
        result = MonitorResult()

        for supplier in queryset:
            if skip_checked_today and supplier.checks.filter(checked_on=today).exists():
                continue
            self.check(supplier, on_date=today, result=result)
        return result

    def check(
        self,
        supplier: Supplier,
        on_date: date | None = None,
        result: MonitorResult | None = None,
    ) -> SupplierCheck:
        """Check a single supplier and record the snapshot."""
        result = result or MonitorResult()
        today = on_date or timezone.localdate()

        try:
            profile = self.client.fetch(supplier.ruc)
        except RucLookupError as exc:
            logger.warning("Lookup failed for %s: %s", supplier.ruc, exc)
            result.failed += 1
            return self._record_failure(supplier, today, str(exc))

        check = self._record_success(supplier, today, profile)
        result.checked += 1
        result.changed += check.changed
        result.with_issues += check.has_issue
        return check

    @transaction.atomic
    def _record_success(
        self, supplier: Supplier, on_date: date, profile: TaxpayerProfile
    ) -> SupplierCheck:
        # Compare against the supplier's mirrored state rather than the previous
        # row, so a failed check in between does not read as a change.
        changed = bool(supplier.last_checked_at) and (
            supplier.status != profile.status or supplier.condition != profile.condition
        )

        check, _ = SupplierCheck.objects.update_or_create(
            supplier=supplier,
            checked_on=on_date,
            defaults={
                "status": profile.status,
                "condition": profile.condition,
                "has_issue": profile.has_issue,
                "changed": changed,
                "previous_status": supplier.status,
                "previous_condition": supplier.condition,
                "succeeded": True,
                "error": "",
                "payload": profile.as_dict(),
            },
        )

        now = timezone.now()
        for field in MIRRORED_FIELDS:
            setattr(supplier, field, getattr(profile, field) or getattr(supplier, field))
        supplier.status = profile.status
        supplier.condition = profile.condition
        supplier.has_issue = profile.has_issue
        supplier.last_checked_at = now
        supplier.last_error = ""
        if changed:
            supplier.last_changed_at = now
            logger.info(
                "Supplier %s changed: %s/%s -> %s/%s",
                supplier.ruc, check.previous_status, check.previous_condition,
                profile.status, profile.condition,
            )
        supplier.save()
        return check

    @transaction.atomic
    def _record_failure(
        self, supplier: Supplier, on_date: date, error: str
    ) -> SupplierCheck:
        """Record the failure without touching the last known good standing."""
        check, _ = SupplierCheck.objects.update_or_create(
            supplier=supplier,
            checked_on=on_date,
            defaults={
                "status": "", "condition": "", "has_issue": False, "changed": False,
                "previous_status": supplier.status,
                "previous_condition": supplier.condition,
                "succeeded": False, "error": error[:500], "payload": {},
            },
        )
        supplier.last_error = error[:500]
        supplier.save(update_fields=["last_error", "updated_at"])
        return check
