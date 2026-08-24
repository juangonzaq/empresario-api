"""Daily compliance snapshots — the source of the trend chart.

One row per company per day; running twice the same day overwrites it, so the
trend never double-counts."""

from __future__ import annotations

from django.utils import timezone

from ..models import ComplianceSnapshot, CompanyObligation
from . import scoring


def create_compliance_snapshot(ruc: str) -> ComplianceSnapshot:
    today = timezone.localdate()
    obligations = list(
        CompanyObligation.objects.filter(account_ruc=ruc).select_related("rule__domain")
    )
    metrics = scoring.compliance_metrics(obligations, today)
    counts = metrics["counts"]

    snapshot, _ = ComplianceSnapshot.objects.update_or_create(
        account_ruc=ruc, snapshot_date=today,
        defaults={
            "overall_score": metrics["score"],
            "applicable_count": counts["applicable"],
            "compliant_count": counts["compliant"],
            "non_compliant_count": counts["non_compliant"],
            "unverified_count": counts["unverified"],
            "overdue_count": counts["overdue"],
            "domain_metrics": metrics["domain_metrics"],
        },
    )
    return snapshot
