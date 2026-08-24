"""Golden tests from FIX_INCOME_TAX_WITHHOLDING.md §4: the mid-year start
case that exposed the projection bug, verified figure by figure against
the hand calculation in that document.

Convention documented there (§4, option A): the pending balance
redistributes across the remaining open months on every recalculation, so
a July run divides by 6 — the yearly total is identical to the reference
spreadsheet's divisor-8 scheme.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from colaboradores.models import Colaborador
from core.testing import TenantAPITestCase

from payroll.models import IncomeTaxMonthlyInput
from payroll.services import runner

RUC = "20604442533"

# §4 of the FIX document: real amounts already paid before the system
# started running (January .. June 2026) and the withholdings practiced.
LOADED_MONTHS = [
    (1, "6000.00", "0.00"),
    (2, "6000.00", "0.00"),
    (3, "6000.00", "2031.49"),
    (4, "8000.00", "1403.94"),
    (5, "10700.00", "0.00"),
    (6, "10700.00", "0.00"),
]


def golden_employee(**overrides) -> Colaborador:
    defaults = {
        "taxpayer_id": RUC,
        "document_number": "70030212",
        "full_name": "GONZALES QUISPE JUAN CARLOS",
        "regimen": "afp",
        "afp": "prima",
        "pension_commission_type": "flow",
        "cuspp": "123456GOLD01",
        "monthly_salary": Decimal("10700.00"),
        "hired_on": datetime.date(2024, 1, 1),
        "has_eps": False,
    }
    return Colaborador.objects.create(**{**defaults, **overrides})


def load_history(person: Colaborador) -> None:
    for month, taxable, withheld in LOADED_MONTHS:
        IncomeTaxMonthlyInput.objects.create(
            colaborador=person, year=2026, month=month,
            taxable_income=Decimal(taxable), withheld=Decimal(withheld),
        )


class GoldenCaseTests(TenantAPITestCase):
    """FIX doc §4 — V11: the July 2026 run must reproduce every figure."""

    def run_july(self, person: Colaborador):
        period = runner.create_period(RUC, 2026, 7)
        return period.entries.get(colaborador=person)

    def test_july_payslip_pays_the_statutory_bonus_automatically(self):
        person = golden_employee()
        entry = self.run_july(person)
        codes = {
            line.concept.code: line.amount
            for line in entry.lines.select_related("concept")
        }
        # Full semester worked: one computable salary, plus 9 % (no EPS).
        self.assertEqual(codes["STATUTORY_BONUS"], Decimal("10700.00"))
        self.assertEqual(codes["EXTRAORDINARY_BONUS"], Decimal("963.00"))
        # Both are taxable but not pensionable (§2.9): the month's taxable
        # base is salary + bonus + extraordinary bonus.
        self.assertEqual(entry.income_tax_base, Decimal("22363.00"))
        self.assertEqual(entry.pension_base, Decimal("10700.00"))

    def test_projection_matches_the_hand_calculation(self):
        person = golden_employee()
        load_history(person)
        person.income_tax_projections.create(
            year=2026, previous_employer_income=Decimal("45893.69"),
        )
        entry = self.run_july(person)

        projection = person.income_tax_projections.get(year=2026)
        # Loaded 47 400 + July 22 363 + Aug..Dec 53 500 + December bonus
        # 11 663 + other employer 45 893.69 − 7 UIT = 142 319.69.
        self.assertEqual(
            projection.projected_annual_income
            + projection.previous_employer_income,
            Decimal("180819.69"),
        )
        self.assertEqual(projection.taxable_income, Decimal("142319.69"))
        # 27 500×8 % + 82 500×14 % + 32 319.69×17 % = 19 244.35 (V11).
        self.assertEqual(projection.annual_tax, Decimal("19244.35"))
        covered = sum(Decimal(d["amount"]) for d in projection.bracket_detail)
        self.assertEqual(covered, Decimal("142319.69"))

        # Pending 19 244.35 − 3 435.43 already practiced = 15 808.92,
        # across the 6 open months (July..December): 2 634.82.
        self.assertEqual(entry.income_tax_withholding, Decimal("2634.82"))
        detail = projection.computation_detail
        self.assertEqual(detail["already_withheld"], "3435.43")
        self.assertEqual(detail["open_months"], [7, 8, 9, 10, 11, 12])
        self.assertFalse(detail["over_withheld"])

        # V9 at a distance: loaded + scheduled covers the annual tax.
        total = sum(
            Decimal(row.effective_amount)
            for row in projection.schedule.all()
        )
        self.assertEqual(total, projection.annual_tax)

    def test_exempt_worker_never_goes_negative(self):
        """FIX doc §4 second case — V12: 1 800 monthly stays below 7 UIT,
        so the withholding is exactly zero, never negative."""
        person = golden_employee(
            document_number="40404040", cuspp="123456GOLD02",
            monthly_salary=Decimal("1800.00"),
        )
        for month in range(1, 7):
            IncomeTaxMonthlyInput.objects.create(
                colaborador=person, year=2026, month=month,
                taxable_income=Decimal("1800.00"), withheld=Decimal("0.00"),
            )
        entry = self.run_july(person)
        projection = person.income_tax_projections.get(year=2026)
        # 21 600 + 2 bonuses of 1 800 × 1.09 = 25 524 < 38 500.
        self.assertEqual(
            projection.projected_annual_income, Decimal("25524.00")
        )
        self.assertEqual(projection.annual_tax, Decimal("0.00"))
        self.assertEqual(entry.income_tax_withholding, Decimal("0.00"))
        self.assertTrue(
            all(row.amount >= 0 for row in projection.schedule.all())
        )

    def test_over_withheld_history_leaves_a_zero_balance(self):
        """V17: loaded withholdings above the annual tax leave the open
        months at zero — never a negative withholding — and flag it."""
        person = golden_employee(
            document_number="50505050", cuspp="123456GOLD03",
            monthly_salary=Decimal("2500.00"),
        )
        IncomeTaxMonthlyInput.objects.create(
            colaborador=person, year=2026, month=1,
            taxable_income=Decimal("2500.00"), withheld=Decimal("9000.00"),
        )
        entry = self.run_july(person)
        self.assertEqual(entry.income_tax_withholding, Decimal("0.00"))
        projection = person.income_tax_projections.get(year=2026)
        self.assertTrue(projection.computation_detail["over_withheld"])

    def test_manual_override_pins_the_month_and_redistributes_the_rest(self):
        """§7.1 of the FIX doc: a pinned month is immutable for the
        engine; only the remaining balance moves to the other months."""
        person = golden_employee(
            document_number="60606060", cuspp="123456GOLD04",
        )
        load_history(person)
        entry = self.run_july(person)
        projection = person.income_tax_projections.get(year=2026)
        july = projection.schedule.get(month=7)
        july.override_amount = Decimal("1976.12")
        july.override_reason = "Reparto pactado con divisor 8"
        july.save()

        runner.recalculate_entry(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.income_tax_withholding, Decimal("1976.12"))

        projection.refresh_from_db()
        # The other five open months absorb what July no longer covers,
        # and the year still reconciles to the annual tax (V9).
        total = sum(
            Decimal(row.effective_amount)
            for row in projection.schedule.all()
        )
        self.assertEqual(total, projection.annual_tax)


class AnnualMatrixTests(TenantAPITestCase):
    """The accountant's spreadsheet, served by the API: per-employee
    monthly income, deduction, brackets and withholding schedule."""

    def test_annual_view_mirrors_the_spreadsheet_row(self):
        from django.urls import reverse

        person = golden_employee(
            document_number="70707070", cuspp="123456GOLD05",
        )
        load_history(person)
        person.income_tax_projections.create(
            year=2026, previous_employer_income=Decimal("45893.69"),
        )
        runner.create_period(RUC, 2026, 7)

        response = self.client.get(reverse("payroll:tax-annual", args=[2026]))
        self.assertEqual(response.status_code, 200)
        row = next(
            r for r in response.data["rows"]
            if r["document_number"] == "70707070"
        )
        income = row["monthly_income"]
        # Real for the loaded past, computed for July (salary + bonus +
        # extraordinary bonus), projected for the future months.
        self.assertEqual(income["1"], "6000.00")
        self.assertEqual(income["4"], "8000.00")
        self.assertEqual(income["7"], "22363.00")
        self.assertEqual(income["8"], "10700.00")
        self.assertEqual(income["12"], "22363.00")  # salary + Dec bonus
        self.assertEqual(str(row["total_income"]), "180819.69")
        self.assertEqual(str(row["annual_tax"]), "19244.35")
        julio = next(s for s in row["schedule"] if s["month"] == 7)
        self.assertEqual(str(julio["effective_amount"]), "2634.82")
