"""Executive overview: deterministic aggregation over cases and analyses.

No LLM call happens here — the headline is templated from real counts, so the
dashboard never pays tokens nor risks an invented number.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from sunat_mailbox.models import Message

from ..models import (
    AnalysisStatus, Case, CaseEvent, CaseStatus, MessageAnalysis, Priority,
)

RISK_ORDER = {
    Priority.CRITICAL: 0, Priority.HIGH: 1,
    Priority.MEDIUM: 2, Priority.INFORMATIONAL: 3,
}

UPCOMING_DAYS = 30


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def build_headline(counts: dict[str, int]) -> str:
    attention = counts["open_cases"]
    parts = [
        f"{attention} {_plural(attention, 'caso requiere', 'casos requieren')} atención"
    ]
    if counts["critical_cases"]:
        n = counts["critical_cases"]
        parts.append(f"{n} {_plural(n, 'es crítico', 'son críticos')}")
    if counts["pending_decisions"]:
        n = counts["pending_decisions"]
        parts.append(
            f"{n} {_plural(n, 'necesita decisión', 'necesitan decisión')} del CEO"
        )
    if counts["upcoming_deadlines"]:
        n = counts["upcoming_deadlines"]
        parts.append(f"{n} {_plural(n, 'tiene plazo próximo', 'tienen plazos próximos')}")
    sentence = ", ".join(parts) + "."
    informational = counts["informational_messages"]
    sentence += (
        f" Se clasificaron {informational} mensajes como informativos y sin "
        "acción requerida."
    )
    return sentence


def _case_row(case: Case) -> dict[str, Any]:
    return {
        "id": str(case.id),
        "title": case.title,
        "risk": case.risk,
        "status": case.status,
        "requires_decision": case.requires_decision,
        "responsible": case.responsible,
        "next_action": case.next_action,
        "exposure_amount": case.exposure_amount,
        "deadline": case.deadline,
        "message_count": case.messages.count(),
    }


def build_overview(taxpayer_id: str) -> dict[str, Any]:
    cases = Case.objects.filter(taxpayer_id=taxpayer_id)
    open_cases = cases.open()
    today = date.today()
    horizon = today + timedelta(days=UPCOMING_DAYS)

    analyses = MessageAnalysis.objects.filter(message__taxpayer_id=taxpayer_id)
    done = analyses.filter(status=AnalysisStatus.DONE)
    informational = done.filter(
        priority=Priority.INFORMATIONAL, requires_action=False
    )

    upcoming_qs = open_cases.filter(
        deadline__gte=today, deadline__lte=horizon
    ).order_by("deadline")

    counts = {
        "open_cases": open_cases.count(),
        "critical_cases": open_cases.filter(risk=Priority.CRITICAL).count(),
        "pending_decisions": open_cases.filter(requires_decision=True).count(),
        "upcoming_deadlines": upcoming_qs.count(),
        "unassigned_cases": open_cases.filter(responsible="").count(),
        "unattended_cases": open_cases.filter(status=CaseStatus.UNREVIEWED).count(),
        "in_management": open_cases.exclude(status=CaseStatus.UNREVIEWED).count(),
        "resolved_cases": cases.filter(status=CaseStatus.RESOLVED).count(),
        "informational_messages": informational.count(),
        "analyzed_messages": done.count(),
        "pending_messages": Message.objects.for_taxpayer(taxpayer_id).count()
        - done.count(),
        # Lectura is reported apart from prioridad on purpose: unread ≠ urgent.
        "unread_messages": Message.objects.for_taxpayer(taxpayer_id).unread().count(),
    }

    exposure = open_cases.aggregate(total=Sum("exposure_amount"))["total"]

    ordered_open = sorted(
        open_cases.prefetch_related("messages"),
        key=lambda c: (RISK_ORDER[c.risk], c.deadline is None, c.deadline or today),
    )

    week_ago = timezone.now() - timedelta(days=7)
    recent_events = [
        {
            "id": str(e.id),
            "case_id": str(e.case_id),
            "case_title": e.case.title,
            "actor": e.actor,
            "kind": e.kind,
            "description": e.description,
            "created_at": e.created_at,
        }
        for e in CaseEvent.objects.filter(
            case__taxpayer_id=taxpayer_id, created_at__gte=week_ago
        ).select_related("case")[:10]
    ]

    return {
        "taxpayer_id": taxpayer_id,
        "headline": build_headline(counts),
        "counts": counts,
        # Sum of amounts that appear verbatim in documents; identified
        # exposure, not a legal debt figure.
        "exposure_total": exposure,
        "exposure_note": (
            "Suma de montos que aparecen expresamente en los documentos de los "
            "casos abiertos. No es una liquidación de deuda."
        ),
        "top_cases": [_case_row(c) for c in ordered_open[:6]],
        "upcoming_deadlines": [_case_row(c) for c in upcoming_qs[:5]],
        "unattended_cases": [
            _case_row(c)
            for c in open_cases.filter(status=CaseStatus.UNREVIEWED)[:5]
        ],
        "recent_events": recent_events,
        "generated_at": timezone.now(),
    }
