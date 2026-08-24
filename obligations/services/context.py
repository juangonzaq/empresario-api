"""The read-only company context the engine judges against.

Everything here is *read* from models that already own the data — the ficha
RUC snapshot, the payroll roster, the declared summaries, the SUNAT compliance
rating. The context never writes anything; it is the single input the rules see,
and a copy of it is stored on each assessment so a verdict can always be
re-explained with the exact facts it was made from.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any

from django.utils import timezone


@dataclasses.dataclass
class CompanyContext:
    account_ruc: str
    today: datetime.date
    flat: dict[str, Any]
    # Punteros de conveniencia que los evaluadores usan sin pasar por `flat`.
    tax_regime: str = ""
    has_payroll: bool = False
    active_employee_count: int = 0
    declared_periods: list[str] = dataclasses.field(default_factory=list)
    latest_declared_period: str | None = None
    consistency_score: int | None = None
    compliance_category: str = ""
    risk_signals: bool = False

    def get(self, path: str) -> Any:
        """Resolve a dotted field path used in a rule's applicability JSON."""
        return self.flat.get(path)


def _latest_snapshot(ruc: str):
    try:
        from ruc_profile.models import RucSnapshot
    except Exception:  # pragma: no cover - app always present, defensive
        return None
    return (
        RucSnapshot.objects.filter(ruc=ruc, succeeded=True)
        .order_by("-captured_on", "-created_at")
        .first()
    )


def _active_employee_count(ruc: str) -> int:
    try:
        from colaboradores.models import Colaborador
    except Exception:  # pragma: no cover
        return 0
    return Colaborador.objects.filter(taxpayer_id=ruc, is_active=True).count()


def _declared_periods(ruc: str) -> list[str]:
    try:
        from reconciliation.models import DeclaredSummary
    except Exception:  # pragma: no cover
        return []
    return list(
        DeclaredSummary.objects.filter(account_ruc=ruc)
        .order_by("-period").values_list("period", flat=True)[:24]
    )


def _consistency_score(ruc: str) -> int | None:
    try:
        from reconciliation.models import ConsistencyScore
    except Exception:  # pragma: no cover
        return None
    row = ConsistencyScore.objects.filter(account_ruc=ruc).order_by("-period").first()
    return row.score if row else None


def _compliance_category(ruc: str) -> str:
    try:
        from compliance_profile.models import ComplianceRating
    except Exception:  # pragma: no cover
        return ""
    row = ComplianceRating.objects.for_taxpayer(ruc).current().order_by("-period").first()
    return row.rating if row else ""


def build_context(organization) -> CompanyContext:
    """Assemble the context for one organization from the existing models."""
    ruc = organization.ruc
    today = timezone.localdate()

    snapshot = _latest_snapshot(ruc)
    profile = getattr(organization, "business_profile", None)
    active_employees = _active_employee_count(ruc)
    # Headcount: la planilla propia manda; si no hay, la cifra declarada en la
    # ficha RUC (PLAME); y como última señal, lo que la empresa declaró en su
    # perfil (personas que trabajan con ella, más allá del titular).
    declared_workers = getattr(snapshot, "worker_count", 0) or 0
    people_count = getattr(profile, "people_count", 0) or 0
    people_beyond_owner = max(0, people_count - 1)
    worker_count = active_employees or declared_workers or people_beyond_owner
    has_payroll = worker_count > 0

    declared_periods = _declared_periods(ruc)
    consistency_score = _consistency_score(ruc)
    compliance_category = _compliance_category(ruc)
    risk_signals = bool(getattr(snapshot, "has_risk_signals", False))

    flat: dict[str, Any] = {
        "company.ruc": ruc,
        "company.tax_regime": organization.tax_regime or "",
        "company.tax_regime_source": organization.tax_regime_source or "",
        "company.has_payroll": has_payroll,
        "company.worker_count": worker_count,
        "company.active_employee_count": active_employees,
        # Perfil declarado del negocio (opcional): rubro y giro para reglas que
        # dependen de qué hace la empresa, no solo de su régimen.
        "company.sector": getattr(profile, "sector", "") or "",
        "company.offering": getattr(profile, "offering", "") or "",
        "company.people_count": people_count,
        "company.business_age": getattr(profile, "business_age", "") or "",
        "company.declared_workers": declared_workers,
        "company.declared_period_count": len(declared_periods),
        "company.latest_declared_period": declared_periods[0] if declared_periods else None,
        "company.consistency_score": consistency_score,
        "company.compliance_category": compliance_category,
        "company.has_risk_signals": risk_signals,
        "company.has_coactive_debt": bool(getattr(snapshot, "has_coactive_debt", False)),
        "company.has_tax_omissions": bool(getattr(snapshot, "has_tax_omissions", False)),
        # Puede venir como date desde la Ficha RUC; a texto para que el
        # input_snapshot sea JSON puro y portable.
        "company.started_activities_on": (
            str(getattr(snapshot, "started_activities_on", "") or "")
        ),
    }

    return CompanyContext(
        account_ruc=ruc,
        today=today,
        flat=flat,
        tax_regime=organization.tax_regime or "",
        has_payroll=has_payroll,
        active_employee_count=active_employees,
        declared_periods=declared_periods,
        latest_declared_period=declared_periods[0] if declared_periods else None,
        consistency_score=consistency_score,
        compliance_category=compliance_category,
        risk_signals=risk_signals,
    )
