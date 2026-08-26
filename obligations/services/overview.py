"""The single payload that feeds the whole compliance screen.

One endpoint, one query set, so the dashboard never fans out into a dozen
requests. Evaluation is *not* run on every GET: it runs only when the data is
stale (or on explicit recalculation), keeping reads cheap.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from .. import enums
from ..models import CompanyObligation, ComplianceSnapshot
from . import scoring
from .deadlines import days_until, time_state
from .engine import evaluate_company
from .snapshots import create_compliance_snapshot

STALE_AFTER = datetime.timedelta(hours=6)
PRIORITY_LIMIT = 6
TREND_LIMIT = 12


def _is_stale(obligations, now) -> bool:
    if not obligations:
        return True
    last = max((ob.last_evaluated_at for ob in obligations if ob.last_evaluated_at), default=None)
    return last is None or (now - last) > STALE_AFTER


def _obligation_card(ob, today) -> dict:
    return {
        "id": str(ob.id),
        "code": ob.rule.code,
        "title": ob.rule.title,
        "domain": ob.rule.domain.code,
        "domain_name": ob.rule.domain.name,
        "severity": ob.severity,
        "compliance_status": ob.compliance_status,
        "workflow_status": ob.workflow_status,
        "verification_status": ob.verification_status,
        "due_date": ob.due_date,
        "days_until": days_until(ob.due_date, today),
        "time_state": time_state(ob.due_date, today),
        "reason": ob.current_assessment,
        "priority": scoring.obligation_priority(ob, today),
    }


def _sunat_category(ruc: str) -> str:
    try:
        from compliance_profile.models import ComplianceRating
    except Exception:  # pragma: no cover
        return ""
    row = ComplianceRating.objects.for_taxpayer(ruc).current().order_by("-period").first()
    return row.rating if row else ""


def build_overview(organization, *, force: bool = False) -> dict:
    ruc = organization.ruc
    today = timezone.localdate()
    now = timezone.now()

    obligations = list(
        CompanyObligation.objects.filter(account_ruc=ruc).select_related("rule__domain")
    )
    if force or _is_stale(obligations, now):
        evaluate_company(organization)
        create_compliance_snapshot(ruc)
        obligations = list(
            CompanyObligation.objects.filter(account_ruc=ruc).select_related("rule__domain")
        )

    metrics = scoring.compliance_metrics(obligations, today)
    counts = metrics["counts"]
    applicable = [ob for ob in obligations if ob.applicability_status == enums.ApplicabilityStatus.APPLICABLE]

    last_evaluated = max(
        (ob.last_evaluated_at for ob in obligations if ob.last_evaluated_at), default=None
    )

    # Distribución de estados (para la barra segmentada).
    status_distribution = [
        {"status": enums.ComplianceStatus.COMPLIANT, "label": "Cumple", "count": counts["compliant"]},
        {"status": enums.ComplianceStatus.NON_COMPLIANT, "label": "No cumple", "count": counts["non_compliant"]},
        {"status": enums.ComplianceStatus.UNKNOWN, "label": "Por revisar", "count": counts["unknown"]},
    ]

    # Prioridades: lo más urgente primero.
    priority_items = sorted(
        (_obligation_card(ob, today) for ob in applicable),
        key=lambda c: c["priority"], reverse=True,
    )[:PRIORITY_LIMIT]

    # Próximos vencimientos (con fecha, futuros o vencidos), del más cercano.
    upcoming = sorted(
        (_obligation_card(ob, today) for ob in applicable if ob.due_date is not None),
        key=lambda c: c["due_date"],
    )

    # Tendencia desde las fotos diarias.
    trend = list(
        ComplianceSnapshot.objects.filter(account_ruc=ruc)
        .order_by("-snapshot_date")[:TREND_LIMIT]
        .values("snapshot_date", "overall_score")
    )
    trend.reverse()
    trend = [{"date": t["snapshot_date"], "score": t["overall_score"]} for t in trend]

    domains = sorted(metrics["domain_metrics"].values(), key=lambda d: d["score"])

    # Alerta ejecutiva: lo más grave abierto.
    critical_open = [
        c for c in priority_items
        if c["severity"] == enums.Severity.CRITICAL
        and c["compliance_status"] != enums.ComplianceStatus.COMPLIANT
    ]
    executive_alert = None
    if critical_open:
        executive_alert = {
            "level": "critical",
            "title": "Tienes obligaciones críticas por atender",
            "items": [c["title"] for c in critical_open[:3]],
        }
    elif counts["overdue"]:
        executive_alert = {
            "level": "warning",
            "title": f"{counts['overdue']} obligación(es) vencida(s)",
            "items": [c["title"] for c in upcoming if c["time_state"] == "overdue"][:3],
        }

    return {
        "summary": {
            "score": metrics["score"],
            "calculation": metrics["calculation"],
            "applicable": counts["applicable"],
            "compliant": counts["compliant"],
            "non_compliant": counts["non_compliant"],
            "unknown": counts["unknown"],
            "unverified": counts["unverified"],
            "overdue": counts["overdue"],
            "not_applicable": counts["not_applicable"],
            "undetermined": counts["undetermined"],
            "sunat_category": _sunat_category(ruc),
            "last_evaluated_at": last_evaluated,
        },
        "executive_alert": executive_alert,
        "status_distribution": status_distribution,
        "domains": domains,
        "priority_items": priority_items,
        "trend": trend,
        "upcoming_deadlines": upcoming[:8],
        "last_evaluated_at": last_evaluated,
    }
