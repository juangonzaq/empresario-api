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
    has_payroll: bool | None = False
    active_employee_count: int = 0
    declared_periods: list[str] = dataclasses.field(default_factory=list)
    latest_declared_period: str | None = None
    consistency_score: int | None = None
    compliance_category: str = ""
    risk_signals: bool = False
    # Sin ficha RUC sincronizada no sabemos nada de señales de riesgo: los
    # evaluadores deben decir «sin determinar», nunca «cumple».
    has_snapshot: bool = False

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


def _worker_count(*, active_employees: int, declared_workers: int,
                  people_count: int, headcount_known: bool) -> int | None:
    """Headcount: la planilla propia manda; si no hay, la cifra declarada en la
    ficha RUC (PLAME); y como última señal, lo que la empresa declaró en su
    perfil (personas que trabajan con ella, más allá del titular). Si ninguna
    fuente existe todavía, el conteo se desconoce: 0 significaría «sé que no
    tienes trabajadores» y eso nadie lo ha afirmado."""
    if not headcount_known:
        return None
    return active_employees or declared_workers or max(0, people_count - 1)


def build_context(organization) -> CompanyContext:
    """Assemble the context for one organization from the existing models.

    Un hecho que la plataforma no conoce entra como ``None``, nunca como "" o 0:
    la aplicabilidad es ternaria y la ausencia de dato debe producir «por
    determinar», no un falso «no te aplica» (regla central de la matriz de
    responsabilidades)."""
    ruc = organization.ruc
    today = timezone.localdate()

    snapshot = _latest_snapshot(ruc)
    profile = getattr(organization, "business_profile", None)
    profile_answered = profile is not None and profile.completed_at is not None
    active_employees = _active_employee_count(ruc)
    people_count = getattr(profile, "people_count", 0) or 0
    declared_workers = getattr(snapshot, "worker_count", 0) or 0
    worker_count = _worker_count(
        active_employees=active_employees,
        declared_workers=declared_workers,
        people_count=people_count,
        headcount_known=active_employees > 0 or snapshot is not None or profile_answered,
    )
    has_payroll: bool | None = None if worker_count is None else worker_count > 0
    # Si la persona respondió «¿tienes trabajadores en planilla?», eso decide
    # cuando los datos duros no dicen lo contrario: un «sí» con conteo 0 es
    # una planilla que aún no se cargó; un «no» con trabajadores activos no
    # puede ganarle a la planilla real.
    declared_payroll = getattr(profile, "has_payroll", None)
    if declared_payroll is True and not has_payroll:
        has_payroll = True
    elif declared_payroll is False and not active_employees and not declared_workers:
        has_payroll = False
        worker_count = 0

    declared_periods = _declared_periods(ruc)
    consistency_score = _consistency_score(ruc)
    compliance_category = _compliance_category(ruc)
    risk_signals = bool(getattr(snapshot, "has_risk_signals", False))

    flat: dict[str, Any] = {
        "company.ruc": ruc,
        # El prefijo del RUC sí es un hecho cierto: 20 = persona jurídica.
        "company.is_juridical": ruc.startswith("20"),
        "company.tax_regime": organization.tax_regime or None,
        "company.tax_regime_source": organization.tax_regime_source or "",
        "company.has_payroll": has_payroll,
        "company.worker_count": worker_count,
        "company.active_employee_count": active_employees,
        # Perfil declarado del negocio (opcional): rubro, giro y tres hechos
        # tri-estado que abren o cierran ramas enteras del catálogo.
        # Rubros y objetivos son listas (la persona puede elegir varios). Una
        # lista vacía es «no lo ha dicho» (None), no «ningún rubro». Las
        # reglas usan `contains`; `company.sector` sigue siendo el principal.
        "company.sectors": list(getattr(profile, "sectors", None) or []) or None,
        "company.sector": getattr(profile, "sector", "") or None,
        "company.goals": list(getattr(profile, "goals", None) or []) or None,
        "company.offering": getattr(profile, "offering", "") or None,
        "company.sells_to_consumers": getattr(profile, "sells_to_consumers", None),
        "company.has_premises": getattr(profile, "has_premises", None),
        "company.sells_online": getattr(profile, "sells_online", None),
        "company.people_count": people_count,
        "company.business_age": getattr(profile, "business_age", "") or "",
        "company.declared_workers": declared_workers,
        "company.declared_period_count": len(declared_periods),
        "company.latest_declared_period": declared_periods[0] if declared_periods else None,
        "company.consistency_score": consistency_score,
        "company.compliance_category": compliance_category,
        # Señales de la ficha RUC: sin snapshot son None (desconocidas), no
        # False. «No hay dato» y «no hay deuda» son cosas distintas.
        "company.has_ficha_ruc": snapshot is not None,
        "company.has_risk_signals": risk_signals if snapshot is not None else None,
        "company.has_coactive_debt": (
            bool(snapshot.has_coactive_debt) if snapshot is not None else None
        ),
        "company.has_tax_omissions": (
            bool(snapshot.has_tax_omissions) if snapshot is not None else None
        ),
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
        has_snapshot=snapshot is not None,
    )
