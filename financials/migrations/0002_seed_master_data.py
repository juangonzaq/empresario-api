"""Seed: statement structure, category tree, ratio catalog and tax rates
(spec §4). All of it is data — editable from the Maestros screen and the
admin — the seed only spares the company from typing national parameters.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import migrations

# statement, code, name, line_type, formula, section, order, is_base
INCOME_LINES = [
    ("GROSS_SALES", "Venta bruta", "detail", "", "operating", 10, False),
    ("SALES_DEDUCTIONS", "Devoluciones, descuentos y bonificaciones", "detail", "", "operating", 20, False),
    ("NET_SALES", "Venta neta", "subtotal", "GROSS_SALES + SALES_DEDUCTIONS", "operating", 30, True),
    ("COST_OF_SALES_LINE", "Costo de venta", "detail", "", "operating", 40, False),
    ("GROSS_PROFIT", "Utilidad bruta", "subtotal", "NET_SALES + COST_OF_SALES_LINE", "operating", 50, False),
    ("ADMIN_EXPENSES_LINE", "Gastos administrativos", "detail", "", "operating", 60, False),
    ("SELLING_EXPENSES_LINE", "Gastos de ventas", "detail", "", "operating", 70, False),
    ("OPERATING_PROFIT", "Utilidad operativa", "subtotal",
     "GROSS_PROFIT + ADMIN_EXPENSES_LINE + SELLING_EXPENSES_LINE", "operating", 80, False),
    ("OTHER_INCOME_LINE", "Ingresos diversos", "detail", "", "non_operating", 90, False),
    ("FINANCIAL_INCOME_LINE", "Ingresos financieros", "detail", "", "non_operating", 100, False),
    ("FINANCIAL_EXPENSES_LINE", "Gastos financieros", "detail", "", "non_operating", 110, False),
    ("FX_LINE", "Diferencia de cambio", "detail", "", "non_operating", 120, False),
    ("PRE_TAX_PROFIT", "Resultado antes de participaciones e impuestos", "subtotal",
     "OPERATING_PROFIT + OTHER_INCOME_LINE + FINANCIAL_INCOME_LINE + FINANCIAL_EXPENSES_LINE + FX_LINE",
     "non_operating", 130, False),
    ("PROFIT_SHARING_LINE", "Participación de utilidades", "detail", "", "non_operating", 140, False),
    ("INCOME_TAX_LINE", "Impuesto a la renta", "detail", "", "non_operating", 150, False),
    ("NET_INCOME", "Utilidad neta", "total",
     "PRE_TAX_PROFIT + PROFIT_SHARING_LINE + INCOME_TAX_LINE", "non_operating", 160, False),
    ("DEPRECIATION_LINE", "Depreciación (informativa)", "detail", "", "operating", 170, False),
    ("EBITDA", "EBITDA", "subtotal", "OPERATING_PROFIT - DEPRECIATION_LINE", "operating", 180, False),
]

BALANCE_LINES = [
    ("CASH_LINE", "Efectivo y equivalentes", "detail", "", "current_assets", 10, False),
    ("TRADE_RECEIVABLES_LINE", "Cuentas por cobrar comerciales", "detail", "", "current_assets", 20, False),
    ("OTHER_RECEIVABLES_LINE", "Otras cuentas por cobrar", "detail", "", "current_assets", 30, False),
    ("INVENTORY_LINE", "Existencias", "detail", "", "current_assets", 40, False),
    ("PREPAID_LINE", "Gastos pagados por anticipado", "detail", "", "current_assets", 50, False),
    ("TOTAL_CURRENT_ASSETS", "Total activo corriente", "subtotal",
     "CASH_LINE + TRADE_RECEIVABLES_LINE + OTHER_RECEIVABLES_LINE + INVENTORY_LINE + PREPAID_LINE",
     "current_assets", 60, False),
    ("PPE_LINE", "Inmuebles, maquinaria y equipo", "detail", "", "non_current_assets", 70, False),
    ("INTANGIBLES_LINE", "Intangibles", "detail", "", "non_current_assets", 80, False),
    ("OTHER_ASSETS_LINE", "Otros activos", "detail", "", "non_current_assets", 90, False),
    ("TOTAL_NON_CURRENT_ASSETS", "Total activo no corriente", "subtotal",
     "PPE_LINE + INTANGIBLES_LINE + OTHER_ASSETS_LINE", "non_current_assets", 100, False),
    ("TOTAL_ASSETS", "Total activo", "total",
     "TOTAL_CURRENT_ASSETS + TOTAL_NON_CURRENT_ASSETS", "non_current_assets", 110, True),
    ("TRADE_PAYABLES_LINE", "Cuentas por pagar comerciales", "detail", "", "current_liabilities", 120, False),
    ("OTHER_PAYABLES_LINE", "Otras cuentas por pagar", "detail", "", "current_liabilities", 130, False),
    ("FIN_OBLIG_CURRENT_LINE", "Obligaciones financieras corrientes", "detail", "", "current_liabilities", 140, False),
    ("TOTAL_CURRENT_LIABILITIES", "Total pasivo corriente", "subtotal",
     "TRADE_PAYABLES_LINE + OTHER_PAYABLES_LINE + FIN_OBLIG_CURRENT_LINE",
     "current_liabilities", 150, False),
    ("FIN_OBLIG_NON_CURRENT_LINE", "Obligaciones financieras no corrientes", "detail", "",
     "non_current_liabilities", 160, False),
    ("TOTAL_NON_CURRENT_LIABILITIES", "Total pasivo no corriente", "subtotal",
     "FIN_OBLIG_NON_CURRENT_LINE", "non_current_liabilities", 170, False),
    ("TOTAL_LIABILITIES", "Total pasivo", "subtotal",
     "TOTAL_CURRENT_LIABILITIES + TOTAL_NON_CURRENT_LIABILITIES", "non_current_liabilities", 180, False),
    ("SHARE_CAPITAL_LINE", "Capital social", "detail", "", "equity", 190, False),
    ("RETAINED_EARNINGS_LINE", "Resultados acumulados", "detail", "", "equity", 200, False),
    ("PERIOD_RESULT", "Resultado del periodo", "detail", "", "equity", 210, False),
    ("TOTAL_EQUITY", "Total patrimonio", "subtotal",
     "SHARE_CAPITAL_LINE + RETAINED_EARNINGS_LINE + PERIOD_RESULT", "equity", 220, False),
    ("TOTAL_LIABILITIES_EQUITY", "Total pasivo y patrimonio", "total",
     "TOTAL_LIABILITIES + TOTAL_EQUITY", "equity", 230, False),
]

# code, name, statement, line, sign, applies_to, order
CATEGORIES = [
    ("SALES_GROSS", "Venta bruta", "income_statement", "GROSS_SALES", 1, "sales", 10),
    ("SALES_RETURNS", "Devoluciones", "income_statement", "SALES_DEDUCTIONS", -1, "sales", 20),
    ("SALES_DISCOUNTS", "Descuentos concedidos", "income_statement", "SALES_DEDUCTIONS", -1, "sales", 30),
    ("SALES_ALLOWANCES", "Bonificaciones", "income_statement", "SALES_DEDUCTIONS", -1, "sales", 40),
    ("COST_OF_SALES", "Costo de venta", "income_statement", "COST_OF_SALES_LINE", -1, "purchases", 50),
    ("PAYROLL_COST_OF_SALES", "Personal · costo de venta", "income_statement", "COST_OF_SALES_LINE", -1, "purchases", 60),
    ("ADMIN_EXPENSES", "Gastos administrativos", "income_statement", "ADMIN_EXPENSES_LINE", -1, "purchases", 70),
    ("PAYROLL_ADMIN", "Personal · administración", "income_statement", "ADMIN_EXPENSES_LINE", -1, "purchases", 80),
    ("SELLING_EXPENSES", "Gastos de ventas", "income_statement", "SELLING_EXPENSES_LINE", -1, "purchases", 90),
    ("PAYROLL_SELLING", "Personal · ventas", "income_statement", "SELLING_EXPENSES_LINE", -1, "purchases", 100),
    ("OTHER_INCOME", "Ingresos diversos", "income_statement", "OTHER_INCOME_LINE", 1, "both", 110),
    ("FINANCIAL_INCOME", "Ingresos financieros", "income_statement", "FINANCIAL_INCOME_LINE", 1, "both", 120),
    ("FINANCIAL_EXPENSES", "Gastos financieros", "income_statement", "FINANCIAL_EXPENSES_LINE", -1, "purchases", 130),
    ("BANK_FEES", "Comisiones bancarias", "income_statement", "FINANCIAL_EXPENSES_LINE", -1, "purchases", 140),
    ("ITF_TAX", "ITF", "income_statement", "FINANCIAL_EXPENSES_LINE", -1, "purchases", 150),
    ("FX_DIFFERENCE", "Diferencia de cambio", "income_statement", "FX_LINE", 1, "both", 160),
    ("PROFIT_SHARING", "Participación de utilidades", "income_statement", "PROFIT_SHARING_LINE", -1, "purchases", 170),
    ("INCOME_TAX", "Impuesto a la renta", "income_statement", "INCOME_TAX_LINE", -1, "purchases", 180),
    ("DEPRECIATION", "Depreciación", "income_statement", "DEPRECIATION_LINE", -1, "purchases", 190),
    # Balance
    ("CASH", "Efectivo", "balance_sheet", "CASH_LINE", 1, "both", 200),
    ("TRADE_RECEIVABLES", "Cuentas por cobrar", "balance_sheet", "TRADE_RECEIVABLES_LINE", 1, "both", 210),
    ("OTHER_RECEIVABLES", "Otras cuentas por cobrar", "balance_sheet", "OTHER_RECEIVABLES_LINE", 1, "both", 220),
    ("INVENTORY", "Existencias", "balance_sheet", "INVENTORY_LINE", 1, "both", 230),
    ("PREPAID_EXPENSES", "Gastos anticipados", "balance_sheet", "PREPAID_LINE", 1, "both", 240),
    ("PROPERTY_PLANT_EQUIPMENT", "Inmuebles, maquinaria y equipo", "balance_sheet", "PPE_LINE", 1, "both", 250),
    ("INTANGIBLES", "Intangibles", "balance_sheet", "INTANGIBLES_LINE", 1, "both", 260),
    ("OTHER_ASSETS", "Otros activos", "balance_sheet", "OTHER_ASSETS_LINE", 1, "both", 270),
    ("TRADE_PAYABLES", "Cuentas por pagar", "balance_sheet", "TRADE_PAYABLES_LINE", 1, "both", 280),
    ("OTHER_PAYABLES", "Otras cuentas por pagar", "balance_sheet", "OTHER_PAYABLES_LINE", 1, "both", 290),
    ("FINANCIAL_OBLIGATIONS_CURRENT", "Obligaciones financieras corrientes", "balance_sheet", "FIN_OBLIG_CURRENT_LINE", 1, "both", 300),
    ("FINANCIAL_OBLIGATIONS_NON_CURRENT", "Obligaciones financieras no corrientes", "balance_sheet", "FIN_OBLIG_NON_CURRENT_LINE", 1, "both", 310),
    ("SHARE_CAPITAL", "Capital social", "balance_sheet", "SHARE_CAPITAL_LINE", 1, "both", 320),
    ("RETAINED_EARNINGS", "Resultados acumulados", "balance_sheet", "RETAINED_EARNINGS_LINE", 1, "both", 330),
]

# code, name, group, num, den, multiplier, unit, recommendation
RATIOS = [
    ("CURRENT_RATIO", "Liquidez corriente", "liquidity",
     "TOTAL_CURRENT_ASSETS", "TOTAL_CURRENT_LIABILITIES", "1", "times",
     "Se sugiere que sea mayor a 1"),
    ("QUICK_RATIO", "Liquidez ácida", "liquidity",
     "TOTAL_CURRENT_ASSETS - INVENTORY_LINE", "TOTAL_CURRENT_LIABILITIES", "1",
     "times", "Se sugiere que sea mayor a 1"),
    ("CASH_RATIO", "Liquidez severa", "liquidity",
     "CASH_LINE", "TOTAL_CURRENT_LIABILITIES", "1", "times",
     "Se sugiere entre 0.3 y 0.4"),
    ("WORKING_CAPITAL", "Capital de trabajo", "liquidity",
     "TOTAL_CURRENT_ASSETS - TOTAL_CURRENT_LIABILITIES", "", "1", "currency",
     "Se espera creciente"),
    ("DSO", "Rotación de cuentas por cobrar (días)", "management",
     "TRADE_RECEIVABLES_LINE * BASE_DAYS", "NET_SALES * IGV_FACTOR", "1",
     "days", "Menos días es mejor"),
    ("DEBT_TO_ASSETS", "Solvencia del activo (pasivo)", "solvency",
     "TOTAL_LIABILITIES", "TOTAL_ASSETS", "1", "times",
     "Se sugiere no mayor a 0.67"),
    ("EQUITY_TO_ASSETS", "Solvencia del activo (patrimonio)", "solvency",
     "TOTAL_EQUITY", "TOTAL_ASSETS", "1", "times",
     "Se sugiere al menos 0.33"),
    ("DEBT_TO_EQUITY", "Solvencia del patrimonio", "solvency",
     "TOTAL_LIABILITIES", "TOTAL_EQUITY", "1", "times",
     "Se sugiere no mayor a 2"),
    ("INTEREST_COVERAGE", "Cobertura de gastos financieros", "solvency",
     "OPERATING_PROFIT", "FINANCIAL_EXPENSES_LINE_ABS", "1", "times",
     "Se sugiere mayor a 1"),
    ("ROA", "Rentabilidad del activo (ROA)", "profitability",
     "OPERATING_PROFIT", "TOTAL_ASSETS", "100", "percent", ""),
    ("ROE", "Rentabilidad del patrimonio (ROE)", "profitability",
     "NET_INCOME", "TOTAL_EQUITY", "100", "percent", ""),
    ("ASSET_TURNOVER", "Rotación de activos", "profitability",
     "NET_SALES", "TOTAL_ASSETS", "1", "times", ""),
    ("GROSS_MARGIN", "Margen bruto", "profitability",
     "GROSS_PROFIT", "NET_SALES", "100", "percent", ""),
    ("OPERATING_MARGIN", "Margen operativo", "profitability",
     "OPERATING_PROFIT", "NET_SALES", "100", "percent", ""),
    ("NET_MARGIN", "Margen neto", "profitability",
     "NET_INCOME", "NET_SALES", "100", "percent", ""),
]

# ratio, min, max, label, severity, advice
THRESHOLDS = [
    ("CURRENT_RATIO", None, "0.5", "Liquidez crítica", "critical",
     "El pasivo corriente casi duplica al activo corriente."),
    ("CURRENT_RATIO", "0.5", "0.8", "Liquidez baja", "warning", ""),
    ("CURRENT_RATIO", "0.8", "1.2", "Niveles óptimos", "good", ""),
    ("CURRENT_RATIO", "1.2", "1.5", "Liquidez alta", "warning",
     "Revisar rotaciones: puede haber recursos ociosos."),
    ("CURRENT_RATIO", "1.5", None, "Exceso de liquidez", "warning",
     "Exceso de liquidez: afecta la rentabilidad."),
    ("DEBT_TO_ASSETS", None, "0.67", "Endeudamiento sano", "good", ""),
    ("DEBT_TO_ASSETS", "0.67", None, "Endeudamiento alto", "warning",
     "El pasivo financia más de dos tercios del activo."),
    ("DEBT_TO_EQUITY", None, "2", "Apalancamiento razonable", "good", ""),
    ("DEBT_TO_EQUITY", "2", None, "Apalancamiento alto", "warning", ""),
    ("INTEREST_COVERAGE", None, "1", "Cobertura insuficiente", "critical",
     "La utilidad operativa no cubre los gastos financieros."),
    ("INTEREST_COVERAGE", "1", None, "Cobertura adecuada", "good", ""),
]


def seed(apps, schema_editor):
    StatementLine = apps.get_model("financials", "StatementLine")
    TransactionCategory = apps.get_model("financials", "TransactionCategory")
    RatioDefinition = apps.get_model("financials", "RatioDefinition")
    RatioThreshold = apps.get_model("financials", "RatioThreshold")
    TaxRate = apps.get_model("financials", "TaxRate")

    for statement, rows in [
        ("income_statement", INCOME_LINES), ("balance_sheet", BALANCE_LINES)
    ]:
        for code, name, line_type, formula, section, order, is_base in rows:
            StatementLine.objects.get_or_create(
                taxpayer_id="", statement=statement, code=code,
                defaults={
                    "name": name, "line_type": line_type, "formula": formula,
                    "section": section, "display_order": order,
                    "is_percentage_base": is_base,
                },
            )

    lines = {
        (l.statement, l.code): l
        for l in StatementLine.objects.filter(taxpayer_id="")
    }
    for code, name, statement, line_code, sign, applies, order in CATEGORIES:
        TransactionCategory.objects.get_or_create(
            taxpayer_id="", code=code,
            defaults={
                "name": name, "statement": statement,
                "statement_line": lines[(statement, line_code)],
                "sign": sign, "applies_to": applies, "display_order": order,
            },
        )

    for code, name, group, num, den, mult, unit, recommendation in RATIOS:
        RatioDefinition.objects.get_or_create(
            code=code,
            defaults={
                "name": name, "group": group, "numerator_formula": num,
                "denominator_formula": den, "multiplier": Decimal(mult),
                "unit": unit, "recommendation": recommendation,
            },
        )
    ratios = {r.code: r for r in RatioDefinition.objects.all()}
    for code, low, high, label, severity, advice in THRESHOLDS:
        RatioThreshold.objects.get_or_create(
            ratio=ratios[code], label=label,
            defaults={
                "min_value": Decimal(low) if low else None,
                "max_value": Decimal(high) if high else None,
                "severity": severity, "advice": advice,
            },
        )

    TaxRate.objects.get_or_create(
        tax_code="IGV", effective_from=datetime.date(2011, 3, 1),
        defaults={"rate": Decimal("0.18")},
    )
    TaxRate.objects.get_or_create(
        tax_code="ITF", effective_from=datetime.date(2011, 4, 1),
        defaults={"rate": Decimal("0.00005")},
    )


def unseed(apps, schema_editor):
    for name in ["RatioThreshold", "RatioDefinition", "TransactionCategory",
                 "StatementLine", "TaxRate"]:
        model = apps.get_model("financials", name)
        if name in ("TransactionCategory", "StatementLine"):
            model.objects.filter(taxpayer_id="").delete()
        else:
            model.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("financials", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
