"""Grouping analyzed messages into business cases.

Grouping is deterministic (union-find over the document references the
analysis extracted), so the same evidence always produces the same cases.
Synthesis follows the product rules: the case reflects the LATEST
communication — a resolución de conclusión lowers the risk instead of adding
a new alert — and human fields (status, responsible) are never overwritten.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Iterable

from django.db import transaction
from django.db.models import Count, Q

from ..models import (
    AnalysisStatus, Case, CaseEvent, CaseStatus, MessageAnalysis, Priority,
)

logger = logging.getLogger(__name__)

PRIORITY_RANK = {
    Priority.CRITICAL: 0, Priority.HIGH: 1,
    Priority.MEDIUM: 2, Priority.INFORMATIONAL: 3,
}


def normalize_reference(ref: str) -> str:
    """'N° 023-002-2905092' → '023-002-2905092' (uppercased, no filler).

    Legal norms (e.g. 'Resolución de Superintendencia N° 014-2008/SUNAT') are
    dropped: they are cited by thousands of unrelated notifications and would
    merge everything into one giant case.
    """
    cleaned = re.sub(r"(?i)n[°º]?\s*", "", ref)
    cleaned = re.sub(r"[^0-9A-Za-z\-/]", "", cleaned)
    cleaned = cleaned.upper()
    is_norm = "SUNAT" in cleaned or "/" in cleaned or "DECRETO" in cleaned
    has_enough_digits = sum(ch.isdigit() for ch in cleaned) >= 7
    if is_norm or not has_enough_digits or len(cleaned) > 30:
        return ""
    return cleaned


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def _group_analyses(
    analyses: Iterable[MessageAnalysis],
) -> dict[str, list[MessageAnalysis]]:
    """Union analyses that share any normalized document reference."""
    uf = _UnionFind()
    keys: dict[int, list[str]] = {}
    for analysis in analyses:
        refs = sorted({normalize_reference(r) for r in analysis.references} - {""})
        if not refs:
            # No shared reference: the message stands alone.
            refs = [f"MSG:{analysis.message.message_code}"]
        keys[analysis.pk] = refs
        for ref in refs[1:]:
            uf.union(refs[0], ref)

    groups: dict[str, list[MessageAnalysis]] = {}
    for analysis in analyses:
        root = uf.find(keys[analysis.pk][0])
        groups.setdefault(root, []).append(analysis)

    # Stable, human-independent key: the smallest reference in the group.
    stable: dict[str, list[MessageAnalysis]] = {}
    for members in groups.values():
        all_refs = sorted({ref for m in members for ref in keys[m.pk]})
        stable[all_refs[0]] = members
    return stable


def _sort_by_publication(members: list[MessageAnalysis]) -> list[MessageAnalysis]:
    return sorted(
        members,
        key=lambda a: (a.message.published_at is None, a.message.published_at),
    )


def _synthesize(case: Case, members: list[MessageAnalysis]) -> None:
    """Fill AI-derived fields from the members. Human fields are untouched."""
    ordered = _sort_by_publication(members)
    latest = ordered[-1]
    primary = min(members, key=lambda a: PRIORITY_RANK[a.priority])

    reference = next(
        (normalize_reference(r) for r in primary.references if normalize_reference(r)),
        "",
    )
    title = primary.comm_type or "Comunicación SUNAT"
    if reference:
        title = f"{title} {reference}"

    # The latest communication defines the current risk and next step: a
    # conclusión (informational) closes the loop instead of alerting again.
    case.title = title[:255]
    case.summary = latest.summary
    case.why_it_matters = latest.why_it_matters
    case.risk = latest.priority
    case.requires_decision = latest.requires_action
    case.next_action = latest.next_action if latest.requires_action else ""
    case.confidence = latest.confidence
    case.tribute = latest.tribute or primary.tribute
    case.tax_period = latest.tax_period or primary.tax_period

    with_amount = [m for m in members if m.amount is not None]
    if with_amount:
        richest = max(with_amount, key=lambda m: m.amount)
        case.exposure_amount = richest.amount
        case.exposure_source = richest.amount_source
    else:
        case.exposure_amount = None
        case.exposure_source = ""

    # El plazo del caso es el más próximo por vencer; si todos pasaron, el
    # último conocido queda como referencia histórica.
    today = date.today()
    deadlines = [m for m in members if m.legal_deadline]
    upcoming = [m for m in deadlines if m.legal_deadline >= today]
    if upcoming:
        chosen = min(upcoming, key=lambda m: m.legal_deadline)
    elif deadlines:
        chosen = max(deadlines, key=lambda m: m.legal_deadline)
    else:
        chosen = None
    case.deadline = chosen.legal_deadline if chosen else None
    case.deadline_source = chosen.deadline_source if chosen else ""


def _is_case_worthy(members: list[MessageAnalysis]) -> bool:
    """Informational-only groups never become cases — no noise, no fake tasks."""
    return any(
        m.priority != Priority.INFORMATIONAL or m.requires_action for m in members
    )


@transaction.atomic
def rebuild_cases(taxpayer_id: str) -> dict[str, int]:
    """Recompute the cases for a taxpayer from the stored analyses."""
    analyses = list(
        MessageAnalysis.objects.filter(
            status=AnalysisStatus.DONE, message__taxpayer_id=taxpayer_id
        ).select_related("message")
    )
    groups = {
        key: members
        for key, members in _group_analyses(analyses).items()
        if _is_case_worthy(members)
    }

    created = updated = 0
    for group_key, members in groups.items():
        case, was_created = Case.objects.get_or_create(
            taxpayer_id=taxpayer_id, group_key=group_key[:255]
        )
        previous_risk = case.risk
        _synthesize(case, members)
        case.save()

        message_ids = {m.message_id for m in members}
        existing = set(case.messages.values_list("id", flat=True))
        new_ids = message_ids - existing
        if new_ids:
            case.messages.add(*new_ids)

        if was_created:
            created += 1
            CaseEvent.objects.create(
                case=case, kind="created",
                description=f"Caso creado a partir de {len(members)} mensaje(s).",
            )
        else:
            updated += 1
            if new_ids:
                CaseEvent.objects.create(
                    case=case, kind="message_added",
                    description=f"{len(new_ids)} mensaje(s) relacionados agregados.",
                )
            if previous_risk != case.risk:
                CaseEvent.objects.create(
                    case=case, kind="risk_changed",
                    description=f"Riesgo actualizado de {previous_risk} a {case.risk}.",
                )

    # Groups can merge when a new document reveals a shared reference. Cases
    # whose key disappeared and that nobody touched are removed; human-managed
    # ones are kept for review.
    stale = Case.objects.filter(taxpayer_id=taxpayer_id).exclude(
        group_key__in=list(groups.keys())
    )
    deleted = (
        stale.annotate(
            human_events=Count("events", filter=~Q(events__actor="sistema"))
        )
        .filter(status=CaseStatus.UNREVIEWED, responsible="", human_events=0)
        .delete()[0]
    )

    return {"created": created, "updated": updated, "deleted": deleted}
