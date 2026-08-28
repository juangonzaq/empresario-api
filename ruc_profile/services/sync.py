"""Records full RUC profiles as snapshots."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from suppliers.services.ruc_client import RucLookupError

from ..models import LegalRepresentative, RucSection, RucSnapshot, WorkerHeadcount
from .client import FullProfile, RucProfileClient
from .constants import (
    SECTION_BRANCHES,
    SECTION_LEGAL_REPRESENTATIVES,
    SECTION_WORKERS,
    SECTIONS,
    SECTIONS_BY_KEY,
)
from .parsers import count_branches, parse_legal_representatives, parse_worker_rows

logger = logging.getLogger(__name__)

# The profile is reviewed monthly, so a snapshot stays current for about that long.
DEFAULT_MAX_AGE_DAYS = 25


@dataclass
class ProfileSyncResult:
    captured: int = 0
    changed: int = 0
    with_risk: int = 0
    failed: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.captured} captured, {self.changed} changed, "
            f"{self.with_risk} with risk signals, {self.skipped} still fresh, "
            f"{self.failed} failed"
        )


class RucProfileSynchronizer:
    """Captures the full SUNAT profile for a set of RUCs."""

    def __init__(self, client: RucProfileClient | None = None):
        self.client = client or RucProfileClient()

    def run(
        self,
        rucs: list[str],
        on_date: date | None = None,
        max_age_days: int | None = DEFAULT_MAX_AGE_DAYS,
    ) -> ProfileSyncResult:
        today = on_date or timezone.localdate()
        result = ProfileSyncResult()

        pending = [ruc for ruc in rucs if not self._is_fresh(ruc, today, max_age_days)]
        result.skipped = len(rucs) - len(pending)

        for ruc in pending:
            try:
                profile = self.client.fetch_full_profile(ruc, SECTIONS)
            except RucLookupError as exc:
                logger.warning("Profile capture failed for %s: %s", ruc, exc)
                result.failed += 1
                self._record_failure(ruc, today, str(exc))
                continue

            snapshot = self._record(ruc, today, profile)
            result.captured += 1
            result.changed += snapshot.changed
            result.with_risk += snapshot.has_risk_signals
        return result

    def _is_fresh(self, ruc: str, on_date: date, max_age_days: int | None) -> bool:
        if max_age_days is None:
            return False
        cutoff = on_date - timedelta(days=max_age_days)
        return RucSnapshot.objects.filter(
            ruc=ruc, succeeded=True, captured_on__gte=cutoff
        ).exists()

    @transaction.atomic
    def _record(self, ruc: str, on_date: date, profile: FullProfile) -> RucSnapshot:
        taxpayer = profile.taxpayer
        signals = self._risk_signals(profile)
        headcounts = self._headcounts(profile)
        latest = headcounts[-1] if headcounts else None

        previous = (
            RucSnapshot.objects.filter(ruc=ruc, succeeded=True)
            .exclude(captured_on=on_date)
            .order_by("-captured_on")
            .first()
        )
        changes = self._diff(previous, taxpayer, signals) if previous else []

        snapshot, _ = RucSnapshot.objects.update_or_create(
            ruc=ruc,
            captured_on=on_date,
            defaults={
                "business_name": taxpayer.business_name,
                "trade_name": taxpayer.trade_name,
                "taxpayer_type": taxpayer.taxpayer_type,
                "status": taxpayer.status,
                "condition": taxpayer.condition,
                "fiscal_address": taxpayer.fiscal_address,
                "economic_activities": taxpayer.economic_activities,
                "electronic_invoicing": taxpayer.electronic_invoicing,
                "registries": taxpayer.registries,
                "registered_on": taxpayer.registered_on,
                "started_activities_on": taxpayer.started_activities_on,
                **signals,
                "has_risk_signals": any(signals.values()),
                "worker_count": latest["workers"] if latest else None,
                "latest_worker_period": latest["period"] if latest else "",
                "branch_count": self._branch_count(profile),
                "changed": bool(changes),
                "change_summary": "; ".join(changes),
                "succeeded": True,
                "error": "",
            },
        )

        self._save_sections(snapshot, profile)
        self._save_headcounts(snapshot, headcounts)
        self._save_representatives(snapshot, profile)

        if changes:
            logger.info("RUC %s changed: %s", ruc, "; ".join(changes))
        return snapshot

    def _risk_signals(self, profile: FullProfile) -> dict[str, bool]:
        def has_data(key: str) -> bool:
            section = profile.section(key)
            return bool(section and section.has_data)

        def answered_yes(key: str) -> bool:
            section = profile.section(key)
            return bool(section and section.answer)

        return {
            "has_coactive_debt": has_data("coactive_debt"),
            "has_tax_omissions": has_data("tax_omissions"),
            "has_probatory_acts": has_data("probatory_acts"),
            "reactiva_peru_debt": answered_yes("reactiva_peru"),
            "covid_guarantee_debt": answered_yes("covid_guarantee"),
        }

    def _diff(self, previous: RucSnapshot, taxpayer, signals: dict[str, bool]) -> list[str]:
        changes = []
        for field, current in (("status", taxpayer.status), ("condition", taxpayer.condition)):
            if getattr(previous, field) != current:
                changes.append(f"{field}: {getattr(previous, field)!r} -> {current!r}")
        for field, current in signals.items():
            if getattr(previous, field) != current:
                changes.append(f"{field}: {getattr(previous, field)} -> {current}")
        return changes

    def _branch_count(self, profile: FullProfile) -> int | None:
        # Sin la sección (error o no pedida) no se sabe: None, no cero.
        section = profile.section(SECTION_BRANCHES)
        return count_branches(section) if section else None

    def _headcounts(self, profile: FullProfile) -> list[dict]:
        section = profile.section(SECTION_WORKERS)
        rows = parse_worker_rows(section) if section else []
        return sorted(rows, key=lambda row: row["period"])

    def _save_sections(self, snapshot: RucSnapshot, profile: FullProfile) -> None:
        snapshot.sections.all().delete()
        records = [
            RucSection(
                snapshot=snapshot, key=data.key, label=data.label, title=data.title,
                has_data=data.has_data, answer=data.answer,
                tables=data.table_payload(), text=data.text[:8000],
            )
            for data in profile.sections.values()
        ]
        records += [
            RucSection(
                snapshot=snapshot, key=key,
                label=SECTIONS_BY_KEY[key].label if key in SECTIONS_BY_KEY else key,
                has_data=False, error=error,
            )
            for key, error in profile.errors.items()
        ]
        RucSection.objects.bulk_create(records)

    def _save_headcounts(self, snapshot: RucSnapshot, rows: list[dict]) -> None:
        snapshot.headcounts.all().delete()
        WorkerHeadcount.objects.bulk_create(
            [WorkerHeadcount(snapshot=snapshot, **row) for row in rows]
        )

    def _save_representatives(self, snapshot: RucSnapshot, profile: FullProfile) -> None:
        section = profile.section(SECTION_LEGAL_REPRESENTATIVES)
        rows = parse_legal_representatives(section) if section else []
        snapshot.legal_representatives.all().delete()
        LegalRepresentative.objects.bulk_create(
            [LegalRepresentative(snapshot=snapshot, **row) for row in rows]
        )

    @transaction.atomic
    def _record_failure(self, ruc: str, on_date: date, error: str) -> RucSnapshot:
        snapshot, _ = RucSnapshot.objects.update_or_create(
            ruc=ruc,
            captured_on=on_date,
            defaults={"succeeded": False, "error": error[:1000], "changed": False},
        )
        return snapshot
