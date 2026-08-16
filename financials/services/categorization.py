"""Categorization engine (spec §6): rules → counterparty default →
history → document-kind heuristic. Each level learns from the previous
one; the goal is that after a few months nothing needs a human.

Text-similarity over glosas (§6.2 step 4) is deferred: with the current
sources the counterparty history covers the same ground at much lower
complexity. The cascade slot stays documented for when descriptions carry
more signal.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from ..models import (
    CategorizationRule, CategorizationStatus, FinancialTransaction,
    TransactionCategory, TransactionDirection,
)

OPERATORS = {
    "equals": lambda value, expected: str(value) == str(expected),
    "contains": lambda value, expected: str(expected).lower() in str(value).lower(),
    "starts_with": lambda value, expected: str(value).lower().startswith(str(expected).lower()),
    "regex": lambda value, expected: re.search(str(expected), str(value)) is not None,
    "in": lambda value, expected: str(value) in [str(v) for v in expected],
    "gte": lambda value, expected: Decimal(str(value)) >= Decimal(str(expected)),
    "lte": lambda value, expected: Decimal(str(value)) <= Decimal(str(expected)),
}

RULE_FIELDS = {
    "counterparty_tax_id", "counterparty_name", "document_kind",
    "description", "net_amount_pen", "source", "direction", "currency",
}

# History threshold (§6.2 step 3): the dominant category of a RUC must
# cover ≥ 80 % of at least 3 confirmed cases to become a suggestion.
HISTORY_MIN_CASES = 3
HISTORY_MIN_SHARE = Decimal("0.8")


def rule_matches(rule: CategorizationRule, transaction: FinancialTransaction) -> bool:
    for condition in rule.conditions:
        field = condition.get("field")
        op = OPERATORS.get(condition.get("op"))
        if field not in RULE_FIELDS or op is None:
            return False
        try:
            if not op(getattr(transaction, field), condition.get("value")):
                return False
        except Exception:
            return False
    return True


INVOICE_SOURCES = {
    "sunat_sales", "sunat_purchases", "manual_sale", "manual_purchase",
}


def _heuristic(
    transaction: FinancialTransaction,
) -> tuple[str, str, bool] | None:
    """§6.2 step 5 — by document kind and direction.

    Returns ``(category_code, reason, auto_confirm)``. Invoices confirm
    solos apenas se sincronizan — emitidas como venta, recibidas como
    compra — porque esa asociación es correcta en la práctica totalidad de
    los casos; la pantalla de transacciones permite recategorizar
    cualquiera después, que es donde vive la excepción.
    """
    is_invoice = transaction.source in INVOICE_SOURCES
    if transaction.direction == TransactionDirection.INFLOW:
        if transaction.is_credit_note:
            return "SALES_RETURNS", "Nota de crédito de venta", is_invoice
        return "SALES_GROSS", "Factura de venta", is_invoice
    if transaction.source in ("sunat_purchases", "manual_purchase"):
        if transaction.is_credit_note:
            return (
                "PURCHASE_RETURNS", "Nota de crédito de compra", is_invoice
            )
        return "COST_OF_SALES", "Factura de compra", is_invoice
    if transaction.document_kind == "planilla":
        return "PAYROLL_ADMIN", "Planilla", False
    return None


def _history_suggestion(transaction: FinancialTransaction) -> tuple[str, str] | None:
    if not transaction.counterparty_tax_id:
        return None
    since = transaction.accounting_date.replace(year=transaction.accounting_date.year - 1)
    rows = (
        FinancialTransaction.objects.filter(
            taxpayer_id=transaction.taxpayer_id,
            counterparty_tax_id=transaction.counterparty_tax_id,
            categorization_status=CategorizationStatus.CONFIRMED,
            accounting_date__gte=since,
            category__isnull=False,
        )
        .values("category__code")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    total = sum(row["count"] for row in rows)
    if total < HISTORY_MIN_CASES or not rows:
        return None
    top = rows[0]
    if Decimal(top["count"]) / Decimal(total) < HISTORY_MIN_SHARE:
        return None
    return (
        top["category__code"],
        f"{top['count']} comprobantes anteriores de esta contraparte",
    )


def _category(taxpayer_id: str, code: str) -> TransactionCategory | None:
    return (
        TransactionCategory.objects.filter(
            taxpayer_id__in=["", taxpayer_id], code=code, is_active=True
        )
        .order_by("-taxpayer_id")
        .first()
    )


def categorize(transaction: FinancialTransaction) -> FinancialTransaction:
    """One pass of the cascade (§6.2). Confirmed and excluded rows are
    never touched: the human decision is final until the human changes it."""
    if transaction.categorization_status in (
        CategorizationStatus.CONFIRMED, CategorizationStatus.EXCLUDED
    ):
        return transaction

    # 1 — explicit rules, best priority first.
    for rule in CategorizationRule.objects.filter(
        taxpayer_id=transaction.taxpayer_id, is_active=True
    ):
        if rule_matches(rule, transaction):
            transaction.category = rule.category
            if rule.confidence >= 1:
                transaction.categorization_status = CategorizationStatus.CONFIRMED
                transaction.categorized_at = timezone.now()
                transaction.suggestion_reason = f"Regla: {rule.name}"
            else:
                transaction.categorization_status = CategorizationStatus.SUGGESTED
                transaction.suggestion_reason = f"Regla: {rule.name}"
            transaction.save()
            rule.match_count += 1
            rule.last_matched_at = timezone.now()
            rule.save(update_fields=["match_count", "last_matched_at", "updated_at"])
            return transaction

    # 1.5 — invoices confirm as sale/purchase right after the rules: a
    # history-based *suggestion* must never downgrade what the document
    # kind already resolves with certainty. Rules keep priority so the
    # user's own criteria can override the default association.
    heuristic = _heuristic(transaction)
    if heuristic is not None and heuristic[2]:
        code, reason, _ = heuristic
        category = _category(transaction.taxpayer_id, code)
        if category is not None:
            transaction.category = category
            transaction.suggestion_reason = reason
            transaction.categorization_status = CategorizationStatus.CONFIRMED
            transaction.categorized_at = timezone.now()
            transaction.save()
            return transaction

    # 2 — counterparty default.
    if transaction.counterparty and transaction.counterparty.default_category:
        transaction.category = transaction.counterparty.default_category
        transaction.categorization_status = CategorizationStatus.SUGGESTED
        transaction.suggestion_reason = "Categoría habitual de la contraparte"
        transaction.save()
        return transaction

    # 3 — history by RUC.
    history = _history_suggestion(transaction)
    if history is not None:
        code, reason = history
        category = _category(transaction.taxpayer_id, code)
        if category is not None:
            transaction.category = category
            transaction.categorization_status = CategorizationStatus.SUGGESTED
            transaction.suggestion_reason = reason
            transaction.save()
            return transaction

    # 5 — document-kind heuristic as a plain suggestion (the auto-confirm
    # cases already resolved at step 1.5; text similarity stays deferred).
    if heuristic is not None:
        code, reason, _ = heuristic
        category = _category(transaction.taxpayer_id, code)
        if category is not None:
            transaction.category = category
            transaction.suggestion_reason = reason
            transaction.categorization_status = CategorizationStatus.SUGGESTED
            transaction.save()
            return transaction

    # 6 — nothing resolved.
    if transaction.categorization_status != CategorizationStatus.UNCATEGORIZED:
        transaction.categorization_status = CategorizationStatus.UNCATEGORIZED
        transaction.save()
    return transaction


def categorize_pending(taxpayer_id: str) -> dict:
    counts = {"confirmed": 0, "suggested": 0, "uncategorized": 0}
    pending = FinancialTransaction.objects.filter(
        taxpayer_id=taxpayer_id,
        categorization_status__in=[
            CategorizationStatus.UNCATEGORIZED, CategorizationStatus.SUGGESTED
        ],
    ).select_related("counterparty__default_category")
    for transaction in pending:
        categorize(transaction)
        counts[transaction.categorization_status] = (
            counts.get(transaction.categorization_status, 0) + 1
        )
    return counts


def confirm(
    transaction: FinancialTransaction, category: TransactionCategory, user
) -> FinancialTransaction:
    transaction.category = category
    transaction.categorization_status = CategorizationStatus.CONFIRMED
    transaction.categorized_by = user if user and user.is_authenticated else None
    transaction.categorized_at = timezone.now()
    transaction.suggestion_reason = ""
    transaction.save()
    return transaction


def create_rule_from(
    transaction: FinancialTransaction, category: TransactionCategory,
) -> CategorizationRule:
    """§6.2 learning: 'always categorize this supplier as X'."""
    return CategorizationRule.objects.create(
        taxpayer_id=transaction.taxpayer_id,
        priority=50,
        name=f"{transaction.counterparty_name or transaction.counterparty_tax_id} "
             f"→ {category.name}",
        conditions=[
            {"field": "counterparty_tax_id", "op": "equals",
             "value": transaction.counterparty_tax_id},
            {"field": "direction", "op": "equals", "value": transaction.direction},
        ],
        category=category,
        confidence=Decimal("1"),
        created_from_transaction=transaction,
    )


def coverage(taxpayer_id: str, year: int) -> dict:
    """The metric the board must show in the user's face (§15): what
    share of the year's flow is categorized."""
    rows = FinancialTransaction.objects.filter(
        taxpayer_id=taxpayer_id, accounting_date__year=year,
    ).exclude(categorization_status=CategorizationStatus.EXCLUDED)
    total = pending_amount = 0
    confirmed = pending_count = 0
    for row in rows.values(
        "categorization_status", "total_amount_pen"
    ):
        total += 1
        if row["categorization_status"] == CategorizationStatus.CONFIRMED:
            confirmed += 1
        else:
            pending_count += 1
            pending_amount += float(row["total_amount_pen"])
    pct = round(confirmed / total * 100, 1) if total else 100.0
    return {
        "total": total,
        "confirmed": confirmed,
        "pending_count": pending_count,
        "pending_amount_pen": round(pending_amount, 2),
        "categorized_pct": pct,
    }
