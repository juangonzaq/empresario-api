"""The evaluation orchestrator.

``evaluate_company`` runs the catalog against one company and upserts its
obligations. It is deterministic and idempotent: same inputs → same rows. It
never runs on every GET (see ``selectors.overview`` for the freshness gate);
it runs on demand, on a source-data change, or on a schedule.

Precedence for the compliance verdict, strongest last:

1. Applicability (declarative) — if the rule doesn't apply, nothing else runs.
2. The rule's evaluator (or UNKNOWN when there is no evaluator / no data).
3. Registered evidence — upgrades verification, and can confirm compliance.
4. Human workflow — a person marking the obligation COMPLETED attests to it.

Engine-owned fields are refreshed every run; human-owned fields (``owner``,
``workflow_status``) are read but never reset by the engine.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from .. import enums
from ..models import (
    ComplianceRule, CompanyObligation, ObligationAssessment, ObligationEvidence,
)
from . import evaluators
from .applicability import is_applicable
from .context import CompanyContext, build_context

_VERIFICATION_STRENGTH = {
    enums.VerificationStatus.UNVERIFIED: 0,
    enums.VerificationStatus.INFERRED: 1,
    enums.VerificationStatus.SELF_REPORTED: 2,
    enums.VerificationStatus.VERIFIED: 3,
}


def _rule_in_window(rule: ComplianceRule, today: datetime.date) -> bool:
    if rule.valid_from and today < rule.valid_from:
        return False
    if rule.valid_until and today > rule.valid_until:
        return False
    return True


def _strongest(a: str, b: str) -> str:
    return a if _VERIFICATION_STRENGTH.get(a, 0) >= _VERIFICATION_STRENGTH.get(b, 0) else b


def _active_evidence(rows, today: datetime.date):
    return [e for e in rows if e.valid_until is None or e.valid_until >= today]


@transaction.atomic
def evaluate_company(organization, *, user=None, force: bool = False) -> dict:
    """Evaluate every active rule for this company and upsert its obligations.
    Returns a small summary. ``force`` is accepted for symmetry; the engine is
    cheap and always runs when called."""
    ctx = build_context(organization)
    ruc = ctx.account_ruc
    today = ctx.today
    now = timezone.now()

    rules = list(
        ComplianceRule.objects.filter(is_active=True).select_related("domain")
    )
    existing = {
        ob.rule_id: ob
        for ob in CompanyObligation.objects.filter(account_ruc=ruc)
    }
    evidence_by_ob: dict = {}
    for e in ObligationEvidence.objects.filter(company_obligation__account_ruc=ruc):
        evidence_by_ob.setdefault(e.company_obligation_id, []).append(e)

    touched = 0
    for rule in rules:
        if not _rule_in_window(rule, today):
            continue
        ob = existing.get(rule.id)
        applies = is_applicable(rule.applicability, ctx)

        if not applies:
            _apply_and_save(
                organization, rule, ob, ctx, user, now,
                applicability=enums.ApplicabilityStatus.NOT_APPLICABLE,
                compliance=enums.ComplianceStatus.UNKNOWN,
                verification=enums.VerificationStatus.UNVERIFIED,
                source=enums.EvaluationSource.RULE_ENGINE,
                reason="No aplica a tu empresa según tu perfil.",
                due_date=None,
            )
            touched += 1
            continue

        # 2) Veredicto del evaluador (o UNKNOWN si no hay).
        fn = evaluators.get_evaluator(rule.evaluator_key) if rule.evaluator_key else None
        verdict = fn(ctx, rule) if fn else evaluators.Verdict(
            reason="Requiere que confirmes su estado o adjuntes evidencia.",
        )
        compliance = verdict.compliance_status
        verification = verdict.verification_status
        source = verdict.evaluation_source
        reason = verdict.reason
        due_date = verdict.due_date

        # 3) Evidencia registrada.
        ev = _active_evidence(evidence_by_ob.get(ob.id, []), today) if ob else []
        if ev:
            best = max(ev, key=lambda e: _VERIFICATION_STRENGTH.get(e.verification_status, 0))
            verification = _strongest(verification, best.verification_status)
            if compliance != enums.ComplianceStatus.NON_COMPLIANT:
                compliance = enums.ComplianceStatus.COMPLIANT
                source = enums.EvaluationSource.EVIDENCE

        # 4) Decisión humana (marcó la obligación como completada).
        if ob and ob.workflow_status == enums.WorkflowStatus.COMPLETED \
                and compliance != enums.ComplianceStatus.NON_COMPLIANT:
            compliance = enums.ComplianceStatus.COMPLIANT
            verification = _strongest(verification, enums.VerificationStatus.SELF_REPORTED)
            source = enums.EvaluationSource.USER

        _apply_and_save(
            organization, rule, ob, ctx, user, now,
            applicability=enums.ApplicabilityStatus.APPLICABLE,
            compliance=compliance, verification=verification, source=source,
            reason=reason, due_date=due_date,
        )
        touched += 1

    return {"ruc": ruc, "rules_evaluated": touched, "evaluated_at": now.isoformat()}


def _apply_and_save(organization, rule, ob, ctx: CompanyContext, user, now, *,
                    applicability, compliance, verification, source, reason, due_date) -> None:
    ruc = ctx.account_ruc
    created = ob is None
    if created:
        ob = CompanyObligation(account_ruc=ruc, rule=rule, severity=rule.default_severity)

    changed = (
        created
        or ob.applicability_status != applicability
        or ob.compliance_status != compliance
        or ob.verification_status != verification
    )

    ob.applicability_status = applicability
    ob.compliance_status = compliance
    ob.verification_status = verification
    ob.severity = rule.default_severity
    ob.applicability_reason = reason if applicability == enums.ApplicabilityStatus.NOT_APPLICABLE else ob.applicability_reason
    ob.current_assessment = reason
    ob.due_date = due_date
    ob.last_evaluated_at = now
    if compliance == enums.ComplianceStatus.COMPLIANT and ob.completed_at is None:
        ob.completed_at = now
    if compliance != enums.ComplianceStatus.COMPLIANT:
        ob.completed_at = None
    ob.save()

    # Historial: solo cuando el veredicto cambia, para no inflar la tabla.
    if changed:
        ObligationAssessment.objects.create(
            company_obligation=ob,
            rule_version=rule.version,
            applicability_status=applicability,
            compliance_status=compliance,
            verification_status=verification,
            evaluation_source=source,
            reason=reason,
            input_snapshot=ctx.flat,
            evaluated_by=user,
        )


def evaluate_company_by_ruc(ruc: str, *, user=None) -> dict | None:
    from accounts.models import Organization

    org = Organization.objects.filter(ruc=ruc).first()
    if org is None:
        return None
    return evaluate_company(org, user=user)
