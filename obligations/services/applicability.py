"""Evaluate a rule's declarative applicability against a company context.

The expression is data, never code. Shape::

    {"all": [{"field": "company.tax_regime", "operator": "eq", "value": "RMT"},
             {"field": "company.worker_count", "operator": "gt", "value": 0}]}

``all`` / ``any`` may nest. An empty expression means the rule always applies.

Evaluation is **ternary** (true / false / unknown): a fact the platform does
not know yet is ``None`` in the context, and comparing against ``None`` yields
*unknown*, never ``False``. Absence of data must produce a question for the
user ("por determinar"), not the conclusion "no te aplica" — concluding from
silence is how compliance screens lie. Combinators follow Kleene logic:
``true AND unknown = unknown`` but ``false AND unknown = false``, and the
mirror for OR, so an explicit fact can still settle the rule even with gaps.

Unknown *operators* (a malformed rule) still fail closed to ``False``: a typo
in the catalog must never surface as a question to every company.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .context import CompanyContext

# Cómo preguntarle a la persona por cada hecho que puede faltar. La clave es el
# `field` usado en las reglas; el texto se inserta en «Falta saber …».
FIELD_QUESTION: dict[str, str] = {
    "company.tax_regime": "tu régimen tributario (se detecta al conectar SUNAT)",
    "company.sector": "el rubro de tu negocio",
    "company.sectors": "el rubro de tu negocio",
    "company.offering": "qué vendes principalmente",
    "company.worker_count": "cuántas personas trabajan contigo",
    "company.has_payroll": "si tienes trabajadores a tu cargo",
    "company.sells_to_consumers": "si vendes al consumidor final",
    "company.has_premises": "si atiendes en un local físico",
    "company.sells_online": "si vendes por internet",
}


@dataclasses.dataclass
class ApplicabilityResult:
    """``value`` is True / False / None (unknown). ``missing`` lists the fields
    whose absence kept the answer unknown — the questions to ask."""

    value: bool | None
    missing: list[str] = dataclasses.field(default_factory=list)


_OPERATORS: dict[str, Any] = {
    "truthy": lambda left, right: bool(left),
    "falsy": lambda left, right: not bool(left),
    "eq": lambda left, right: left == right,
    "ne": lambda left, right: left != right,
    "gt": lambda left, right: left > right,
    "gte": lambda left, right: left >= right,
    "lt": lambda left, right: left < right,
    "lte": lambda left, right: left <= right,
    "in": lambda left, right: left in (right or []),
    "nin": lambda left, right: left not in (right or []),
    # Para hechos que son listas (rubros, objetivos): ¿está el valor entre ellos?
    "contains": lambda left, right: right in (left or []),
    "ncontains": lambda left, right: right not in (left or []),
}


def _compare(left: Any, operator: str, right: Any) -> bool | None:
    # `exists` pregunta si el dato se conoce: con None la respuesta es un No
    # rotundo, no una incógnita.
    if operator == "exists":
        return left is not None
    fn = _OPERATORS.get(operator)
    if fn is None:
        return False  # operador desconocido: regla malformada, no una pregunta.
    if left is None:
        return None
    try:
        return fn(left, right)
    except TypeError:
        return False


def _combine(values: list[bool | None], *, is_all: bool) -> bool | None:
    """Kleene AND/OR: un hecho explícito decide aunque otros falten."""
    if is_all:
        if any(v is False for v in values):
            return False
        if any(v is None for v in values):
            return None
        return True
    if any(v is True for v in values):
        return True
    if any(v is None for v in values):
        return None
    return False


def _eval_node(node: Any, ctx: CompanyContext, missing: list[str]) -> bool | None:
    if not node:
        return True
    if isinstance(node, dict) and "all" in node:
        return _combine([_eval_node(c, ctx, missing) for c in node["all"]], is_all=True)
    if isinstance(node, dict) and "any" in node:
        return _combine([_eval_node(c, ctx, missing) for c in node["any"]], is_all=False)
    if isinstance(node, dict) and "field" in node:
        field = node["field"]
        result = _compare(ctx.get(field), node.get("operator", "eq"), node.get("value"))
        if result is None and field not in missing:
            missing.append(field)
        return result
    return False


def evaluate_applicability(applicability: dict | None, ctx: CompanyContext) -> ApplicabilityResult:
    """Ternary verdict for one rule (empty expression = always applies).

    ``missing`` puede incluir hechos que al final no bloquearon la respuesta
    (p. ej. un OR decidido por otra rama); solo importa cuando ``value is None``.
    """
    missing: list[str] = []
    value = _eval_node(applicability or {}, ctx, missing)
    return ApplicabilityResult(value=value, missing=missing if value is None else [])


def missing_facts_question(missing: list[str]) -> str:
    """Human sentence for an unknown verdict: what we need and where to say it."""
    labels = [FIELD_QUESTION.get(f, f) for f in missing]
    if not labels:
        return "Nos falta información de tu empresa para saber si esto te aplica."
    return (
        "Falta saber " + "; ".join(labels) +
        ". Respóndelo en el perfil de tu negocio (Perfil › Empresas › tu empresa)."
    )


def is_applicable(applicability: dict | None, ctx: CompanyContext) -> bool:
    """Binary convenience: strictly True only when the facts prove it applies."""
    return evaluate_applicability(applicability, ctx).value is True
