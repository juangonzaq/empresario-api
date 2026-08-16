"""Statement engine (spec §7): income statement and balance sheet built
from confirmed transactions and derived balances — never typed.

Formulas of subtotal lines are data (``StatementLine.formula``); the
evaluator only knows ``+`` and ``-`` over line codes, which is all a
statement structure needs and small enough to audit by eye.
"""

from __future__ import annotations

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from ..models import (
    AccountBalance, CategorizationStatus, FinancialTransaction,
    ManualBalanceEntry, SettlementStatus, StatementKind, StatementLine,
    StatementSection, TransactionCategory, TransactionDirection,
)

CENTS = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def pct(numerator: Decimal, denominator: Decimal | None):
    """Division guard (§7.1): a zero denominator returns None and the
    front shows a dash. An Infinity in a chart breaks the whole axis."""
    if not denominator:
        return None
    return float(
        (numerator / denominator * 100).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    )


def statement_lines(taxpayer_id: str, statement: str) -> list[StatementLine]:
    """Company lines shadow the global structure by code."""
    lines: dict[str, StatementLine] = {}
    rows = StatementLine.objects.filter(
        taxpayer_id__in=["", taxpayer_id], statement=statement
    ).order_by("taxpayer_id", "display_order")
    for line in rows:
        lines[line.code] = line
    return sorted(lines.values(), key=lambda l: l.display_order)


def evaluate_formula(formula: str, values: dict[str, Decimal]) -> Decimal:
    """`NET_SALES + COST_OF_SALES - ADMIN_EXPENSES` over computed codes.
    Unknown codes count zero: a structure typo must not crash the board,
    it shows as a wrong subtotal that the fixture test catches."""
    total = Decimal("0")
    sign = 1
    for token in formula.replace("+", " + ").replace("-", " - ").split():
        if token == "+":
            sign = 1
        elif token == "-":
            sign = -1
        else:
            total += sign * values.get(token, Decimal("0"))
    return total


# ------------------------------------------------------- income statement
def income_statement(taxpayer_id: str, year: int) -> dict:
    """Monthly income statement for one fiscal year (§7.1), with vertical
    and horizontal analysis. Amounts come ONLY from CONFIRMED
    transactions; the coverage metric says how much is missing."""
    lines = statement_lines(taxpayer_id, StatementKind.INCOME_STATEMENT)

    # category code → (statement line code, sign) map, company shadowing.
    categories = TransactionCategory.objects.filter(
        taxpayer_id__in=["", taxpayer_id],
        statement=StatementKind.INCOME_STATEMENT,
        statement_line__isnull=False,
    ).select_related("statement_line").order_by("taxpayer_id")
    category_map = {c.id: c for c in categories}

    rows = (
        FinancialTransaction.objects.filter(
            taxpayer_id=taxpayer_id,
            accounting_date__year=year,
            categorization_status=CategorizationStatus.CONFIRMED,
            category_id__in=category_map.keys(),
        )
        .values("category_id", "accounting_date__month")
        .annotate(total=Sum("net_amount_pen"))
    )

    monthly: dict[str, dict[int, Decimal]] = {}
    for row in rows:
        category = category_map[row["category_id"]]
        line_code = category.statement_line.code
        month = row["accounting_date__month"]
        bucket = monthly.setdefault(line_code, {})
        bucket[month] = bucket.get(month, Decimal("0")) + (
            Decimal(row["total"]) * category.sign
        )

    previous = _annual_detail_totals(taxpayer_id, year - 1, category_map)

    result_lines = []
    annual: dict[str, Decimal] = {}
    monthly_values: dict[int, dict[str, Decimal]] = {m: {} for m in range(1, 13)}
    base_code = next((l.code for l in lines if l.is_percentage_base), None)

    for line in lines:
        if line.line_type == "detail":
            months = {
                m: money(monthly.get(line.code, {}).get(m, Decimal("0")))
                for m in range(1, 13)
            }
        else:
            months = {
                m: money(evaluate_formula(line.formula, monthly_values[m]))
                for m in range(1, 13)
            }
        total = money(sum(months.values(), Decimal("0")))
        annual[line.code] = total
        for m in range(1, 13):
            monthly_values[m][line.code] = months[m]

        prior = previous.get(line.code)
        result_lines.append({
            "code": line.code,
            "name": line.name,
            "line_type": line.line_type,
            "is_percentage_base": line.is_percentage_base,
            "months": {str(m): float(v) for m, v in months.items()},
            "total": float(total),
            "previous_total": None if prior is None else float(prior),
            "horizontal_pct": (
                pct(total - prior, abs(prior)) if prior else None
            ),
        })

    base_total = annual.get(base_code) if base_code else None
    for row in result_lines:
        row["vertical_pct"] = pct(Decimal(str(row["total"])), base_total)

    return {"year": year, "lines": result_lines, "percentage_base": base_code}


def _annual_detail_totals(
    taxpayer_id: str, year: int, category_map: dict
) -> dict[str, Decimal]:
    rows = (
        FinancialTransaction.objects.filter(
            taxpayer_id=taxpayer_id,
            accounting_date__year=year,
            categorization_status=CategorizationStatus.CONFIRMED,
            category_id__in=category_map.keys(),
        )
        .values("category_id")
        .annotate(total=Sum("net_amount_pen"))
    )
    totals: dict[str, Decimal] = {}
    for row in rows:
        category = category_map[row["category_id"]]
        code = category.statement_line.code
        totals[code] = totals.get(code, Decimal("0")) + (
            Decimal(row["total"]) * category.sign
        )
    # subtotals of the previous year, from its own detail
    lines = statement_lines(taxpayer_id, StatementKind.INCOME_STATEMENT)
    values = dict(totals)
    for line in lines:
        if line.line_type != "detail":
            values[line.code] = evaluate_formula(line.formula, values)
    return {code: money(v) for code, v in values.items()}


# ------------------------------------------------------------- balances
def derive_balances(taxpayer_id: str, year: int, month: int) -> None:
    """§5.2 — receivables and payables become a consequence of the
    documents. Cash needs bank movements (deferred), so it stays manual."""
    def unsettled(direction: str) -> Decimal:
        end = _month_end(year, month)
        rows = FinancialTransaction.objects.filter(
            taxpayer_id=taxpayer_id,
            direction=direction,
            accounting_date__lte=end,
            source__in=["sunat_sales", "sunat_purchases", "manual_sale", "manual_purchase"],
        ).exclude(settlement_status=SettlementStatus.SETTLED)
        total = Decimal("0")
        for row in rows.values("total_amount_pen", "settled_amount", "is_credit_note"):
            sign = -1 if row["is_credit_note"] else 1
            total += sign * (
                Decimal(row["total_amount_pen"]) - Decimal(row["settled_amount"])
            )
        return money(max(total, Decimal("0")))

    derived = {
        "TRADE_RECEIVABLES": unsettled(TransactionDirection.INFLOW),
        "TRADE_PAYABLES": unsettled(TransactionDirection.OUTFLOW),
    }
    for code, amount in derived.items():
        category = (
            TransactionCategory.objects.filter(
                taxpayer_id__in=["", taxpayer_id], code=code
            ).order_by("-taxpayer_id").first()
        )
        if category is None:
            continue
        AccountBalance.objects.update_or_create(
            taxpayer_id=taxpayer_id, year=year, month=month, category=category,
            defaults={
                "amount": amount, "source": "derived",
                "computed_at": timezone.now(),
            },
        )


def _month_end(year: int, month: int) -> datetime.date:
    import calendar

    return datetime.date(year, month, calendar.monthrange(year, month)[1])


def balance_sheet(taxpayer_id: str, year: int, month: int) -> dict:
    """§7.2 — every line says whether it is derived or typed, and the
    equality check is blocking: an unbalanced sheet invalidates every
    ratio built on it."""
    derive_balances(taxpayer_id, year, month)
    lines = statement_lines(taxpayer_id, StatementKind.BALANCE_SHEET)

    amounts: dict[str, Decimal] = {}
    origins: dict[str, str] = {}
    for balance in AccountBalance.objects.filter(
        taxpayer_id=taxpayer_id, year=year, month=month
    ).select_related("category__statement_line"):
        if balance.category.statement_line:
            code = balance.category.statement_line.code
            amounts[code] = amounts.get(code, Decimal("0")) + balance.amount
            origins[code] = "derived"
    for manual in ManualBalanceEntry.objects.filter(
        taxpayer_id=taxpayer_id, year=year, month=month
    ).select_related("category__statement_line"):
        if manual.category.statement_line:
            code = manual.category.statement_line.code
            amounts[code] = amounts.get(code, Decimal("0")) + manual.amount
            origins[code] = "manual"

    # The period result comes from the income statement, never typed.
    result = income_statement(taxpayer_id, year)
    net_income = next(
        (Decimal(str(l["total"])) for l in result["lines"] if l["code"] == "NET_INCOME"),
        Decimal("0"),
    )
    amounts["PERIOD_RESULT"] = net_income
    origins["PERIOD_RESULT"] = "derived"

    values: dict[str, Decimal] = dict(amounts)
    out_lines = []
    for line in lines:
        if line.line_type == "detail":
            amount = money(amounts.get(line.code, Decimal("0")))
        else:
            amount = money(evaluate_formula(line.formula, values))
        values[line.code] = amount
        out_lines.append({
            "code": line.code,
            "name": line.name,
            "line_type": line.line_type,
            "section": line.section,
            "amount": float(amount),
            "origin": origins.get(line.code, "derived" if line.line_type != "detail" else "manual"),
        })

    total_assets = values.get("TOTAL_ASSETS", Decimal("0"))
    total_liabilities_equity = values.get("TOTAL_LIABILITIES_EQUITY", Decimal("0"))
    imbalance = money(total_assets - total_liabilities_equity)
    for row in out_lines:
        row["vertical_pct"] = pct(Decimal(str(row["amount"])), total_assets or None)

    return {
        "year": year,
        "month": month,
        "lines": out_lines,
        # V1 — publishing an unbalanced sheet is refused upstream.
        "is_balanced": imbalance == 0,
        "imbalance": float(imbalance),
    }
