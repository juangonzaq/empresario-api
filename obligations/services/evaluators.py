"""Controlled registry of compliance evaluators.

A rule names an ``evaluator_key``; that key resolves here to a function that
looks at the read-only context and returns a verdict. This is the *only* place
"how do we know if this is met" logic lives — never in the database.

An evaluator judges **compliance**, not applicability (applicability is the
rule's declarative expression). When we genuinely can't tell from the data we
hold, evaluators return ``UNKNOWN`` / ``UNVERIFIED`` with an honest reason —
never a green checkmark we can't back up. The screen must never present an
inference as a verified fact, and it never presents a gap as evasion.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Callable

from .. import enums
from .context import CompanyContext


@dataclasses.dataclass
class Verdict:
    compliance_status: str = enums.ComplianceStatus.UNKNOWN
    verification_status: str = enums.VerificationStatus.UNVERIFIED
    evaluation_source: str = enums.EvaluationSource.RULE_ENGINE
    reason: str = ""
    due_date: datetime.date | None = None


Evaluator = Callable[[CompanyContext, object], Verdict]
_REGISTRY: dict[str, Evaluator] = {}


def evaluator(key: str) -> Callable[[Evaluator], Evaluator]:
    def wrap(fn: Evaluator) -> Evaluator:
        _REGISTRY[key] = fn
        return fn
    return wrap


def get_evaluator(key: str) -> Evaluator | None:
    return _REGISTRY.get(key)


# --------------------------------------------------------------------------- #
# Deadline helpers
# --------------------------------------------------------------------------- #

def _last_closed_period(today: datetime.date) -> str:
    """The tax period whose monthly declaration is currently due: last month."""
    year, month = today.year, today.month
    if month == 1:
        return f"{year - 1}12"
    return f"{year}{month - 1:02d}"


# --------------------------------------------------------------------------- #
# Evaluators
# --------------------------------------------------------------------------- #

@evaluator("tax_monthly_declaration")
def tax_monthly_declaration(ctx: CompanyContext, rule) -> Verdict:
    """Did the company file its monthly IGV/renta declaration for the last
    closed period? Inferred from the declared summaries we already hold."""
    expected = _last_closed_period(ctx.today)
    if not ctx.declared_periods:
        return Verdict(
            compliance_status=enums.ComplianceStatus.UNKNOWN,
            verification_status=enums.VerificationStatus.UNVERIFIED,
            reason="Aún no tenemos declaraciones sincronizadas para confirmar el periodo.",
        )
    if expected in ctx.declared_periods:
        return Verdict(
            compliance_status=enums.ComplianceStatus.COMPLIANT,
            verification_status=enums.VerificationStatus.INFERRED,
            reason=f"Encontramos la declaración del periodo {expected} en tus datos.",
        )
    return Verdict(
        compliance_status=enums.ComplianceStatus.NON_COMPLIANT,
        verification_status=enums.VerificationStatus.INFERRED,
        reason=f"No vemos la declaración del periodo {expected}. "
               "Revísalo: puede estar pendiente o sin sincronizar.",
    )


@evaluator("consistency_control")
def consistency_control(ctx: CompanyContext, rule) -> Verdict:
    """Preventive control: are your declared figures consistent with your CPE?
    Reads the reconciliation consistency score already computed elsewhere."""
    score = ctx.consistency_score
    if score is None:
        return Verdict(reason="Corre la conciliación para medir la consistencia de tus datos.")
    if score >= 70:
        return Verdict(
            compliance_status=enums.ComplianceStatus.COMPLIANT,
            verification_status=enums.VerificationStatus.INFERRED,
            reason=f"Tu puntaje de consistencia es {score}/100: sin diferencias mayores.",
        )
    return Verdict(
        compliance_status=enums.ComplianceStatus.NON_COMPLIANT,
        verification_status=enums.VerificationStatus.INFERRED,
        reason=f"Tu puntaje de consistencia es {score}/100: hay diferencias que conviene revisar.",
    )


@evaluator("payroll_registration")
def payroll_registration(ctx: CompanyContext, rule) -> Verdict:
    """T-Registro: workers must be registered before they start. We can't read
    the T-Registro, so we flag it for confirmation when there is payroll."""
    if ctx.active_employee_count > 0:
        return Verdict(
            compliance_status=enums.ComplianceStatus.UNKNOWN,
            verification_status=enums.VerificationStatus.INFERRED,
            reason=f"Tienes {ctx.active_employee_count} persona(s) en planilla. "
                   "Confirma que estén dadas de alta en el T-Registro.",
        )
    return Verdict(
        compliance_status=enums.ComplianceStatus.UNKNOWN,
        reason="Registra a tus trabajadores en el T-Registro antes de su primer día.",
    )


@evaluator("risk_signals_clear")
def risk_signals_clear(ctx: CompanyContext, rule) -> Verdict:
    """Preventive: does the ficha RUC show risk signals (deuda coactiva,
    omisiones)? Read straight from the latest snapshot."""
    if not ctx.has_snapshot:
        return Verdict(
            compliance_status=enums.ComplianceStatus.UNKNOWN,
            verification_status=enums.VerificationStatus.UNVERIFIED,
            reason="Aún no tenemos tu ficha RUC sincronizada para revisar señales de riesgo.",
        )
    if ctx.get("company.has_coactive_debt") or ctx.get("company.has_tax_omissions"):
        return Verdict(
            compliance_status=enums.ComplianceStatus.NON_COMPLIANT,
            verification_status=enums.VerificationStatus.INFERRED,
            reason="Tu ficha RUC muestra señales de riesgo (deuda coactiva u omisiones). "
                   "Regularízalas cuanto antes.",
        )
    if ctx.risk_signals:
        return Verdict(
            compliance_status=enums.ComplianceStatus.UNKNOWN,
            verification_status=enums.VerificationStatus.INFERRED,
            reason="Tu ficha RUC tiene alguna señal marcada; conviene revisarla.",
        )
    return Verdict(
        compliance_status=enums.ComplianceStatus.COMPLIANT,
        verification_status=enums.VerificationStatus.INFERRED,
        reason="Tu ficha RUC no muestra señales de riesgo.",
    )


@evaluator("plame_declaration")
def plame_declaration(ctx: CompanyContext, rule) -> Verdict:
    """¿Está presentada la PLAME del periodo que toca? Se lee de la consulta
    de declaraciones de SUNAT, así que el veredicto es verificado."""
    try:
        from sunat_declaraciones.services import presentaciones_por_periodo
        from sunat_declaraciones.services.plazos import vencimiento_de
    except Exception:  # pragma: no cover
        return Verdict(reason="Sin acceso a las declaraciones.")
    presentadas = presentaciones_por_periodo(ctx.account_ruc)
    if not presentadas:
        return Verdict(reason="Aún no se han sincronizado las declaraciones de SUNAT.")
    expected = _last_closed_period(ctx.today)
    vence = vencimiento_de(ctx.account_ruc, expected)
    p = presentadas.get(expected, {}).get("0601")
    if p:
        return Verdict(
            compliance_status=enums.ComplianceStatus.COMPLIANT,
            verification_status=enums.VerificationStatus.VERIFIED,
            reason=f"PLAME del periodo {expected} presentada el {p['fecha']:%d/%m/%Y} (orden {p['nro_orden']}).",
            due_date=vence,
        )
    if vence and vence >= ctx.today:
        return Verdict(
            compliance_status=enums.ComplianceStatus.UNKNOWN,
            verification_status=enums.VerificationStatus.VERIFIED,
            reason=f"La PLAME de {expected} todavía no figura; vence el {vence:%d/%m/%Y}.",
            due_date=vence,
        )
    return Verdict(
        compliance_status=enums.ComplianceStatus.NON_COMPLIANT,
        verification_status=enums.VerificationStatus.VERIFIED,
        reason=f"No aparece la PLAME del periodo {expected} en SUNAT" + (f" (venció el {vence:%d/%m/%Y})." if vence else "."),
        due_date=vence,
    )


@evaluator("tax_payments_current")
def tax_payments_current(ctx: CompanyContext, rule) -> Verdict:
    """Sin omisiones ni deuda del 621 (alertas abiertas de declaraciones) y
    sin multas pagadas en los últimos 12 meses."""
    try:
        from finance_analytics.models import FinanceAlert
        from sunat_declaraciones.models import DeclaracionPresentada, Formulario
        from sunat_declaraciones.services.tributos import tributos_de
    except Exception:  # pragma: no cover
        return Verdict(reason="Sin acceso a las declaraciones.")
    if not DeclaracionPresentada.objects.de(ctx.account_ruc).exists():
        return Verdict(reason="Aún no se han sincronizado las declaraciones de SUNAT.")
    abiertas = list(
        FinanceAlert.objects.filter(account_ruc=ctx.account_ruc, alert_type__startswith="declaracion_").open()
    )
    desde = f"{ctx.today.year - 1}{ctx.today.month:02d}"
    multas = 0
    for b in DeclaracionPresentada.objects.de(ctx.account_ruc).formulario(Formulario.BOLETA).filter(periodo__gte=desde):
        multas += sum(1 for t in tributos_de(b.constancia) if t["clase"] == "multa")
    if abiertas:
        return Verdict(
            compliance_status=enums.ComplianceStatus.NON_COMPLIANT,
            verification_status=enums.VerificationStatus.INFERRED,
            reason=abiertas[0].title + (f" (+{len(abiertas) - 1} más)" if len(abiertas) > 1 else ""),
        )
    if multas:
        return Verdict(
            compliance_status=enums.ComplianceStatus.COMPLIANT,
            verification_status=enums.VerificationStatus.VERIFIED,
            reason=f"Sin deuda ni omisiones detectadas, pero pagaste {multas} multa(s) en los últimos 12 meses.",
        )
    return Verdict(
        compliance_status=enums.ComplianceStatus.COMPLIANT,
        verification_status=enums.VerificationStatus.VERIFIED,
        reason="Declaraciones presentadas y pagadas según la consulta de SUNAT; sin multas en 12 meses.",
    )
