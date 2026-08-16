"""Ratio engine (spec §8): formulas declared in ``RatioDefinition`` over
statement-line codes, plus the variables the management ratios need —
``IGV_FACTOR`` (from the TaxRate master, §4.2) and ``BASE_DAYS`` (with the
partial-year adjustment of §8.4).

Numerator and denominator are returned next to every value: a ratio the
user cannot decompose is a number, not a diagnosis (§5.5).
"""

from __future__ import annotations

import datetime
from decimal import ROUND_HALF_UP, Decimal

from ..models import (
    CategorizationStatus, FinancialSettings, FinancialTransaction,
    RatioDefinition, StatementKind, TransactionDirection,
)
from .ingest import igv_rate_on
from .statements import balance_sheet, evaluate_formula, income_statement, pct


def _round6(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def settings_for(taxpayer_id: str) -> FinancialSettings:
    settings, _ = FinancialSettings.objects.get_or_create(taxpayer_id=taxpayer_id)
    return settings


def _months_with_data(taxpayer_id: str, year: int) -> int:
    months = (
        FinancialTransaction.objects.filter(
            taxpayer_id=taxpayer_id, accounting_date__year=year,
            categorization_status=CategorizationStatus.CONFIRMED,
        )
        .dates("accounting_date", "month")
    )
    return len(list(months))


def base_days(taxpayer_id: str, year: int) -> tuple[int, bool]:
    """§8.4 — the partial-year trap: a 360-day base over six months of
    data doubles the rotation days. Returns (days, is_partial)."""
    settings = settings_for(taxpayer_id)
    today = datetime.date.today()
    complete = year < today.year
    if complete or not settings.use_elapsed_days_for_partial_year:
        return settings.days_per_year, False
    months = _months_with_data(taxpayer_id, year)
    if months >= 12:
        return settings.days_per_year, False
    return max(months, 1) * settings.days_per_month, True


def _values_for(taxpayer_id: str, year: int, month: int) -> dict[str, Decimal]:
    """Every statement-line code from both statements, as the variable
    space the ratio formulas evaluate over."""
    values: dict[str, Decimal] = {}
    for line in income_statement(taxpayer_id, year)["lines"]:
        values[line["code"]] = Decimal(str(line["total"]))
    for line in balance_sheet(taxpayer_id, year, month)["lines"]:
        values[line["code"]] = Decimal(str(line["amount"]))
    # Expense lines carry their statement sign (negative); coverage-style
    # ratios need the magnitude, so every code gets an _ABS twin.
    for code, value in list(values.items()):
        values[f"{code}_ABS"] = abs(value)
    return values


def annual_ratios(taxpayer_id: str, year: int, month: int = 12) -> list[dict]:
    days, partial = base_days(taxpayer_id, year)
    igv = igv_rate_on(datetime.date(year, month, 1))
    values = _values_for(taxpayer_id, year, month)
    values["IGV_FACTOR"] = Decimal("1") + igv
    values["BASE_DAYS"] = Decimal(days)

    out = []
    for ratio in RatioDefinition.objects.filter(is_active=True).prefetch_related(
        "thresholds"
    ):
        numerator = _evaluate_expression(ratio.numerator_formula, values)
        denominator = (
            _evaluate_expression(ratio.denominator_formula, values)
            if ratio.denominator_formula else Decimal("1")
        )
        if denominator == 0:
            value = None  # V11: null, never infinity nor zero
        else:
            value = _round6(numerator / denominator * ratio.multiplier)

        threshold = _match_threshold(ratio, value)
        out.append({
            "code": ratio.code,
            "name": ratio.name,
            "group": ratio.group,
            "unit": ratio.unit,
            "value": None if value is None else float(value),
            "numerator": float(numerator),
            "denominator": float(denominator),
            "recommendation": ratio.recommendation,
            "interpretation": ratio.interpretation,
            "severity": threshold.severity if threshold else None,
            "severity_label": threshold.label if threshold else None,
            "advice": threshold.advice if threshold else "",
            # §8.4: a partial base must be said next to the number.
            "base_days": days if ratio.unit == "days" else None,
            "partial_year": partial if ratio.unit == "days" else False,
        })
    return out


def _evaluate_expression(formula: str, values: dict[str, Decimal]) -> Decimal:
    """`TOTAL_CURRENT_ASSETS - INVENTORY`, `NET_SALES * IGV_FACTOR`…
    Supports + - and a single ``*`` factor, which covers the catalog."""
    if "*" in formula:
        left, right = formula.split("*", 1)
        return evaluate_formula(left.strip(), values) * values.get(
            right.strip(), Decimal("1")
        )
    return evaluate_formula(formula, values)


def _match_threshold(ratio: RatioDefinition, value):
    if value is None:
        return None
    for threshold in ratio.thresholds.all():
        low_ok = threshold.min_value is None or value >= threshold.min_value
        high_ok = threshold.max_value is None or value < threshold.max_value
        if low_ok and high_ok:
            return threshold
    return None


# ------------------------------------------------------ monthly management
def monthly_management_ratios(taxpayer_id: str, year: int) -> list[dict]:
    """§8.2 — point balance against accumulated flow: the correct method
    under seasonality. A negative cash cycle is said in words, not painted
    red for being negative."""
    settings = settings_for(taxpayer_id)
    igv_factor = Decimal("1") + igv_rate_on(datetime.date(year, 1, 1))

    monthly = income_statement(taxpayer_id, year)
    sales_by_month = {}
    cogs_by_month = {}
    for line in monthly["lines"]:
        if line["code"] == "NET_SALES":
            sales_by_month = {int(k): Decimal(str(v)) for k, v in line["months"].items()}
        if line["code"] == "COST_OF_SALES_LINE":
            cogs_by_month = {int(k): Decimal(str(v)) for k, v in line["months"].items()}

    purchases_by_month = _purchases_by_month(taxpayer_id, year)

    out = []
    accumulated_sales = accumulated_cogs = accumulated_purchases = Decimal("0")
    for month in range(1, 13):
        accumulated_sales += sales_by_month.get(month, Decimal("0")) * igv_factor
        # COGS lines carry −1 sign in the statement: flip to positive flow.
        accumulated_cogs += abs(cogs_by_month.get(month, Decimal("0"))) * igv_factor
        accumulated_purchases += purchases_by_month.get(month, Decimal("0"))
        if accumulated_sales == 0 and accumulated_purchases == 0:
            continue

        sheet = balance_sheet(taxpayer_id, year, month)
        by_code = {l["code"]: Decimal(str(l["amount"])) for l in sheet["lines"]}
        days_elapsed = Decimal(month * settings.days_per_month)

        def rotation(balance: Decimal, flow: Decimal):
            if flow <= 0:
                return None
            return float(
                (balance * days_elapsed / flow).quantize(Decimal("0.1"))
            )

        dso = rotation(by_code.get("TRADE_RECEIVABLES_LINE", Decimal("0")), accumulated_sales)
        dio = rotation(by_code.get("INVENTORY_LINE", Decimal("0")), accumulated_cogs)
        dpo = rotation(by_code.get("TRADE_PAYABLES_LINE", Decimal("0")), accumulated_purchases)
        ccc = None
        if dso is not None and dpo is not None:
            ccc = round(dso + (dio or 0) - dpo, 1)

        out.append({
            "month": month,
            "dso": dso,
            "dio": dio,
            "dpo": dpo,
            "cash_conversion_cycle": ccc,
            "favorable_financing": ccc is not None and ccc < 0,
        })
    return out


def _purchases_by_month(taxpayer_id: str, year: int) -> dict[int, Decimal]:
    from django.db.models import Sum

    rows = (
        FinancialTransaction.objects.filter(
            taxpayer_id=taxpayer_id,
            accounting_date__year=year,
            direction=TransactionDirection.OUTFLOW,
            source__in=["sunat_purchases", "manual_purchase"],
        )
        .values("accounting_date__month")
        .annotate(total=Sum("total_amount_pen"))
    )
    return {
        row["accounting_date__month"]: Decimal(row["total"]) for row in rows
    }


# ------------------------------------------------------------------- KPIs
KPI_LINES = [
    "NET_SALES", "COST_OF_SALES_LINE", "GROSS_PROFIT", "ADMIN_EXPENSES_LINE",
    "SELLING_EXPENSES_LINE", "OPERATING_PROFIT", "NET_INCOME",
]


def kpis(taxpayer_id: str, year: int) -> dict:
    """§8.3 — monthly series, current year vs previous, plus margins."""
    current = income_statement(taxpayer_id, year)
    previous = income_statement(taxpayer_id, year - 1)

    def series(result: dict) -> dict[str, dict[int, Decimal]]:
        return {
            line["code"]: {int(k): Decimal(str(v)) for k, v in line["months"].items()}
            for line in result["lines"] if line["code"] in KPI_LINES
        }

    now, before = series(current), series(previous)
    months = []
    for month in range(1, 13):
        sales = now.get("NET_SALES", {}).get(month, Decimal("0"))
        row = {"month": month}
        for code in KPI_LINES:
            value = now.get(code, {}).get(month, Decimal("0"))
            prior = before.get(code, {}).get(month, Decimal("0"))
            row[code.lower()] = float(value)
            row[f"{code.lower()}_prev"] = float(prior)
            row[f"{code.lower()}_var_pct"] = pct(value - prior, abs(prior) or None)
        row["gross_margin_pct"] = pct(
            now.get("GROSS_PROFIT", {}).get(month, Decimal("0")), sales or None
        )
        row["operating_margin_pct"] = pct(
            now.get("OPERATING_PROFIT", {}).get(month, Decimal("0")), sales or None
        )
        row["net_margin_pct"] = pct(
            now.get("NET_INCOME", {}).get(month, Decimal("0")), sales or None
        )
        months.append(row)
    return {"year": year, "months": months}
