"""Two different numbers, kept apart on purpose.

* ``compliance_score`` — how much of what applies is met, weighted. This is the
  one the user sees as a percentage. NOT_APPLICABLE is excluded from the base.
* ``priority_score`` — only for *ordering* actions. It blends severity, non-
  compliance, lateness and evidence gaps. It must never be shown as a
  compliance percentage; a critical open item scoring "high priority" is not
  "8% compliant".

Weights come from the rule (``effective_weight``), never hard-coded in the UI.
"""

from __future__ import annotations

import datetime

from .. import enums
from .deadlines import time_state

# Factores del priority_score (solo para ordenar).
_NON_COMPLIANCE_FACTOR = {
    enums.ComplianceStatus.NON_COMPLIANT: 2.0,
    enums.ComplianceStatus.UNKNOWN: 1.3,
    enums.ComplianceStatus.COMPLIANT: 0.2,
}
_TIME_FACTOR = {"overdue": 2.0, "due_soon": 1.5, "upcoming": 1.0, None: 1.0}
_EVIDENCE_FACTOR = {
    enums.VerificationStatus.UNVERIFIED: 1.4,
    enums.VerificationStatus.INFERRED: 1.1,
    enums.VerificationStatus.SELF_REPORTED: 1.05,
    enums.VerificationStatus.VERIFIED: 1.0,
}


def obligation_priority(ob, today: datetime.date) -> float:
    """Higher = attend sooner. Ordering only, never a compliance figure."""
    if ob.applicability_status != enums.ApplicabilityStatus.APPLICABLE:
        return 0.0
    severity = enums.SEVERITY_WEIGHT.get(ob.severity, 2)
    non_compliance = _NON_COMPLIANCE_FACTOR.get(ob.compliance_status, 1.0)
    lateness = _TIME_FACTOR.get(time_state(ob.due_date, today), 1.0)
    evidence = _EVIDENCE_FACTOR.get(ob.verification_status, 1.0)
    return round(severity * non_compliance * lateness * evidence, 3)


def _is_applicable(ob) -> bool:
    return ob.applicability_status == enums.ApplicabilityStatus.APPLICABLE


def _is_compliant(ob) -> bool:
    return ob.compliance_status == enums.ComplianceStatus.COMPLIANT


def compliance_metrics(obligations, today: datetime.date) -> dict:
    """Overall score + counts + per-domain breakdown from a list of obligations
    (prefetch ``rule__domain`` to avoid N+1)."""
    applicable = [ob for ob in obligations if _is_applicable(ob)]

    applicable_weight = sum(ob.rule.effective_weight for ob in applicable)
    compliant_weight = sum(ob.rule.effective_weight for ob in applicable if _is_compliant(ob))
    score = round(compliant_weight / applicable_weight * 100) if applicable_weight else 0

    counts = {
        "applicable": len(applicable),
        "compliant": sum(1 for ob in applicable if _is_compliant(ob)),
        "non_compliant": sum(
            1 for ob in applicable
            if ob.compliance_status == enums.ComplianceStatus.NON_COMPLIANT
        ),
        "unknown": sum(
            1 for ob in applicable
            if ob.compliance_status == enums.ComplianceStatus.UNKNOWN
        ),
        "unverified": sum(
            1 for ob in applicable
            if ob.verification_status == enums.VerificationStatus.UNVERIFIED
        ),
        "overdue": sum(
            1 for ob in applicable if time_state(ob.due_date, today) == "overdue"
        ),
        "not_applicable": sum(
            1 for ob in obligations
            if ob.applicability_status == enums.ApplicabilityStatus.NOT_APPLICABLE
        ),
        # «Por determinar»: falta un hecho del perfil para saber si aplica.
        # Fuera de la base del score (no se castiga ni premia lo desconocido);
        # la pantalla lo convierte en preguntas pendientes.
        "undetermined": sum(
            1 for ob in obligations
            if ob.applicability_status == enums.ApplicabilityStatus.UNKNOWN
        ),
    }

    # Por dominio.
    domains: dict[str, dict] = {}
    for ob in applicable:
        code = ob.rule.domain.code
        d = domains.setdefault(code, {
            "code": code, "name": ob.rule.domain.name,
            "applicable_weight": 0, "compliant_weight": 0,
            "applicable": 0, "compliant": 0, "critical_open": 0,
        })
        w = ob.rule.effective_weight
        d["applicable_weight"] += w
        d["applicable"] += 1
        if _is_compliant(ob):
            d["compliant_weight"] += w
            d["compliant"] += 1
        elif ob.severity == enums.Severity.CRITICAL:
            d["critical_open"] += 1

    domain_metrics = {}
    for code, d in domains.items():
        dscore = round(d["compliant_weight"] / d["applicable_weight"] * 100) if d["applicable_weight"] else 0
        domain_metrics[code] = {
            "code": code, "name": d["name"], "score": dscore,
            "applicable": d["applicable"], "compliant": d["compliant"],
            "critical_open": d["critical_open"],
        }

    return {
        "score": score,
        "calculation": {
            "compliant_weight": compliant_weight,
            "applicable_weight": applicable_weight,
            "method": "WEIGHTED_COMPLIANCE",
        },
        "counts": counts,
        "domain_metrics": domain_metrics,
    }
