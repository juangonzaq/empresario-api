"""Evaluate a rule's declarative applicability against a company context.

The expression is data, never code. Shape::

    {"all": [{"field": "company.tax_regime", "operator": "eq", "value": "RMT"},
             {"field": "company.active_employee_count", "operator": "gt", "value": 0}]}

``all`` / ``any`` may nest. An empty expression means the rule always applies.
Unknown operators or missing fields fail closed (the condition is False), so a
malformed rule never silently marks something applicable.
"""

from __future__ import annotations

from typing import Any

from .context import CompanyContext


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "exists":
        return left is not None
    if operator == "truthy":
        return bool(left)
    if operator == "falsy":
        return not bool(left)
    if left is None:
        # Sin dato no se puede afirmar nada salvo la ausencia, ya cubierta arriba.
        return False
    try:
        if operator == "eq":
            return left == right
        if operator == "ne":
            return left != right
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "lt":
            return left < right
        if operator == "lte":
            return left <= right
        if operator == "in":
            return left in (right or [])
        if operator == "nin":
            return left not in (right or [])
    except TypeError:
        return False
    return False


def _eval_node(node: Any, ctx: CompanyContext) -> bool:
    if not node:
        return True
    if isinstance(node, dict) and "all" in node:
        return all(_eval_node(child, ctx) for child in node["all"])
    if isinstance(node, dict) and "any" in node:
        return any(_eval_node(child, ctx) for child in node["any"])
    if isinstance(node, dict) and "field" in node:
        left = ctx.get(node["field"])
        return _compare(left, node.get("operator", "eq"), node.get("value"))
    return False


def is_applicable(applicability: dict | None, ctx: CompanyContext) -> bool:
    """True when the rule applies to this company (empty expression = always)."""
    return _eval_node(applicability or {}, ctx)
