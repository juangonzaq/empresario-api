"""Seed the master tables (spec §2 and §2.9).

Values are DATA, not law-in-code: every figure here is editable in the
admin and versioned by effective date. The seed only saves each company
from typing the national parameters by hand — never ask a company to load
the UIT (§2 note).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import migrations

EPOCH = datetime.date(2020, 1, 1)

# code, name (payslip label, Spanish), kind, pension, tax, computed, order
CONCEPTS = [
    ("WORKED_SALARY", "Sueldo", "earning", True, True, True, 10),
    ("VACATION", "Vacaciones", "earning", True, True, True, 20),
    ("SICK_LEAVE", "Descanso médico", "earning", True, True, True, 30),
    ("AUTHORIZED_LEAVE", "Licencia con goce", "earning", True, True, True, 40),
    ("VACATION_SALE", "Venta de vacaciones", "earning", True, True, False, 50),
    ("OVERTIME_FIRST", "Horas extra 25 %", "earning", True, True, True, 60),
    ("OVERTIME_ADDITIONAL", "Horas extra 35 %", "earning", True, True, True, 70),
    ("FAMILY_ALLOWANCE", "Asignación familiar", "earning", True, True, True, 80),
    ("BONUS", "Bonificación", "earning", True, True, False, 90),
    ("STATUTORY_BONUS", "Gratificación", "earning", False, True, False, 100),
    ("EXTRAORDINARY_BONUS", "Bonificación extraordinaria", "earning", False, True, True, 110),
    ("TRUNCATED_VACATION", "Vacaciones truncas", "earning", True, True, False, 120),
    ("TRUNCATED_STATUTORY_BONUS", "Gratificación trunca", "earning", False, True, False, 130),
    ("TRUNCATED_EXTRAORDINARY_BONUS", "Bonif. extraordinaria trunca", "earning", False, True, False, 140),
    ("SEVERANCE_DEPOSIT", "Depósito CTS", "earning", False, False, False, 150),
    ("TRUNCATED_SEVERANCE", "CTS trunca", "earning", False, False, False, 160),
    ("INDEMNITY", "Indemnización", "earning", False, False, False, 170),
    ("YEAR_END_BONUS", "Bono de fin de año", "earning", False, True, False, 180),
    ("INCOME_TAX_REFUND", "Devolución de renta", "earning", False, False, False, 190),
    ("TRANSPORT_ALLOWANCE", "Movilidad", "earning", False, False, False, 200),
    ("ABSENCE_DISCOUNT", "Descuento por faltas", "deduction", True, True, True, 300),
    ("PENSION_CONTRIBUTION", "Aporte obligatorio", "deduction", False, False, True, 310),
    ("PENSION_COMMISSION", "Comisión AFP", "deduction", False, False, True, 320),
    ("PENSION_INSURANCE", "Prima de seguro", "deduction", False, False, True, 330),
    ("INCOME_TAX_WITHHOLDING", "Renta de 5.ª categoría", "deduction", False, False, True, 340),
    ("SALARY_ADVANCE", "Adelanto de sueldo", "deduction", False, False, False, 350),
    ("LOAN_INSTALLMENT", "Cuota de préstamo", "deduction", False, False, False, 360),
    ("EPS_CONTRIBUTION", "Aporte EPS", "deduction", False, False, False, 370),
    ("HEALTH_CONTRIBUTION", "EsSalud", "employer_cost", False, False, True, 400),
    ("WORK_RISK_INSURANCE", "SCTR", "employer_cost", False, False, True, 410),
]

# fund, commission_type, mandatory, flow commission, insurance, ceiling
PENSION_RATES = [
    ("onp", "flow", "0.13", "0", "0", None),
    ("habitat", "flow", "0.10", "0.0147", "0.0137", "12209.81"),
    ("habitat", "mixed", "0.10", "0", "0.0137", "12209.81"),
    ("integra", "flow", "0.10", "0.0155", "0.0137", "12209.81"),
    ("integra", "mixed", "0.10", "0", "0.0137", "12209.81"),
    ("prima", "flow", "0.10", "0.0160", "0.0137", "12209.81"),
    ("prima", "mixed", "0.10", "0", "0.0137", "12209.81"),
    ("profuturo", "flow", "0.10", "0.0169", "0.0137", "12209.81"),
    ("profuturo", "mixed", "0.10", "0", "0.0137", "12209.81"),
]

# order, width in UIT (None = open-ended), marginal rate
BRACKETS_2026 = [
    (1, "5", "0.08"),
    (2, "15", "0.14"),
    (3, "15", "0.17"),
    (4, "10", "0.20"),
    (5, None, "0.30"),
]


def seed(apps, schema_editor):
    TaxUnitValue = apps.get_model("payroll", "TaxUnitValue")
    MinimumWage = apps.get_model("payroll", "MinimumWage")
    PensionFundRate = apps.get_model("payroll", "PensionFundRate")
    IncomeTaxBracket = apps.get_model("payroll", "IncomeTaxBracket")
    IncomeTaxSettings = apps.get_model("payroll", "IncomeTaxSettings")
    SocialHealthRate = apps.get_model("payroll", "SocialHealthRate")
    WorkRiskInsuranceRate = apps.get_model("payroll", "WorkRiskInsuranceRate")
    PayrollConcept = apps.get_model("payroll", "PayrollConcept")

    TaxUnitValue.objects.get_or_create(
        year=2026, defaults={"amount": Decimal("5500.00")}
    )
    MinimumWage.objects.get_or_create(
        effective_from=datetime.date(2025, 1, 1),
        defaults={"amount": Decimal("1130.00")},
    )
    SocialHealthRate.objects.get_or_create(
        effective_from=EPOCH,
        defaults={
            "standard_rate": Decimal("0.09"),
            "eps_rate": Decimal("0.0675"),
            "applies_minimum_wage_floor": True,
            "extraordinary_bonus_rate": Decimal("0.09"),
        },
    )
    IncomeTaxSettings.objects.get_or_create(
        year=2026,
        defaults={
            "standard_deduction_in_tax_units": Decimal("7"),
            "months_in_projection": 12,
            "statutory_bonus_count": 2,
        },
    )
    for order, width, rate in BRACKETS_2026:
        IncomeTaxBracket.objects.get_or_create(
            year=2026, order=order,
            defaults={
                "width_in_tax_units": Decimal(width) if width else None,
                "rate": Decimal(rate),
            },
        )
    for fund, commission, mandatory, flow, insurance, ceiling in PENSION_RATES:
        PensionFundRate.objects.get_or_create(
            pension_fund=fund, commission_type=commission, effective_from=EPOCH,
            defaults={
                "mandatory_contribution_rate": Decimal(mandatory),
                "flow_commission_rate": Decimal(flow),
                "insurance_premium_rate": Decimal(insurance),
                "insurable_ceiling": Decimal(ceiling) if ceiling else None,
            },
        )
    WorkRiskInsuranceRate.objects.get_or_create(
        activity_code="nivel_1", effective_from=EPOCH,
        defaults={
            "health_rate": Decimal("0.0053"),
            "pension_rate": Decimal("0.0068"),
        },
    )
    for code, name, kind, pension, tax, computed, order in CONCEPTS:
        PayrollConcept.objects.get_or_create(
            taxpayer_id="", code=code,
            defaults={
                "name": name, "kind": kind,
                "affects_pension_base": pension,
                "affects_income_tax_base": tax,
                "is_computed": computed,
                "display_order": order,
            },
        )


def unseed(apps, schema_editor):
    """Reverse: only the global seed rows, never company data."""
    for model_name in [
        "PayrollConcept", "WorkRiskInsuranceRate", "PensionFundRate",
        "IncomeTaxBracket", "IncomeTaxSettings", "SocialHealthRate",
        "MinimumWage", "TaxUnitValue",
    ]:
        model = apps.get_model("payroll", model_name)
        if model_name == "PayrollConcept":
            model.objects.filter(taxpayer_id="").delete()
        else:
            model.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("payroll", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
