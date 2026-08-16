"""Engine tests: every amount below is verified against a hand
calculation, per the transversal acceptance criterion (spec §11)."""

from __future__ import annotations

import datetime
from decimal import Decimal

from colaboradores.models import Colaborador
from core.testing import TenantAPITestCase

from payroll.models import PayrollStatus
from payroll.services import runner
from payroll.services.master_data import MasterDataMissing, RatesSnapshot
from payroll.services.payslip import amount_in_words, format_pen, render_payslip

RUC = "20604442533"


def employee(**overrides) -> Colaborador:
    defaults = {
        "taxpayer_id": RUC,
        "document_number": str(employee.counter).rjust(8, "4"),
        "full_name": f"Trabajador {employee.counter}",
        "regimen": "onp",
        "monthly_salary": Decimal("3000.00"),
        "hired_on": datetime.date(2024, 1, 1),
    }
    employee.counter += 1
    return Colaborador.objects.create(**{**defaults, **overrides})


employee.counter = 1


def lines_by_code(entry) -> dict:
    return {line.concept.code: line for line in entry.lines.select_related("concept")}


class MasterDataTests(TenantAPITestCase):
    def test_resolves_the_seeded_year(self):
        rates = RatesSnapshot.resolve(RUC, 2026, 8)
        self.assertEqual(rates.tax_unit, Decimal("5500.00"))
        self.assertEqual(rates.minimum_wage, Decimal("1130.00"))
        self.assertIn(("onp", "flow"), rates.pension_rates)
        self.assertIn("WORKED_SALARY", rates.concepts)

    def test_missing_year_blocks_the_run(self):
        """V16: no master data, no run — an explicit refusal, never a
        silently wrong payroll."""
        with self.assertRaises(MasterDataMissing):
            RatesSnapshot.resolve(RUC, 2031, 1)


class MonthlyCalculationTests(TenantAPITestCase):
    """§4 — hand-checked amounts for the representative cases."""

    def run_period(self, year=2026, month=8):
        return runner.create_period(RUC, year, month)

    def entry_of(self, period, colaborador):
        return period.entries.get(colaborador=colaborador)

    def test_onp_full_month(self):
        person = employee()  # ONP, 3000, full month
        entry = self.entry_of(self.run_period(), person)

        self.assertEqual(entry.gross_pay, Decimal("3000.00"))
        self.assertEqual(entry.pension_base, Decimal("3000.00"))
        # ONP 13 %: 390. Commission and premium are zero by master data,
        # not by a code branch.
        self.assertEqual(entry.pension_contribution, Decimal("390.00"))
        self.assertEqual(entry.pension_commission, Decimal("0.00"))
        self.assertEqual(entry.pension_insurance, Decimal("0.00"))
        # Health 9 % over 3000; net 3000 − 390.
        self.assertEqual(entry.health_contribution, Decimal("270.00"))
        self.assertEqual(entry.net_pay, Decimal("2610.00"))
        self.assertEqual(entry.total_employer_cost, Decimal("3270.00"))
        self.assertEqual(entry.total_hours, Decimal("240.00"))

    def test_afp_flow_with_family_allowance(self):
        person = employee(
            regimen="afp", afp="integra", pension_commission_type="flow",
            cuspp="123456ABCDE1", monthly_salary=Decimal("5000.00"),
            receives_family_allowance=True,
        )
        entry = self.entry_of(self.run_period(), person)

        codes = lines_by_code(entry)
        # Family allowance: 10 % of RMV 1130 = 113, never prorated.
        self.assertEqual(codes["FAMILY_ALLOWANCE"].amount, Decimal("113.00"))
        self.assertEqual(entry.pension_base, Decimal("5113.00"))
        # Hand math: 5113 × 10 % = 511.30 · × 1.55 % = 79.25 · × 1.37 % = 70.05
        self.assertEqual(entry.pension_contribution, Decimal("511.30"))
        self.assertEqual(entry.pension_commission, Decimal("79.25"))
        self.assertEqual(entry.pension_insurance, Decimal("70.05"))
        self.assertEqual(entry.net_pay, Decimal("4452.40"))

    def test_mixed_commission_withholds_no_flow_commission(self):
        person = employee(
            regimen="afp", afp="integra", pension_commission_type="mixed",
            cuspp="123456ABCDE2", monthly_salary=Decimal("5000.00"),
        )
        entry = self.entry_of(self.run_period(), person)
        # The balance component is charged by the AFP directly (§4.6).
        self.assertEqual(entry.pension_commission, Decimal("0.00"))
        self.assertEqual(entry.pension_contribution, Decimal("500.00"))

    def test_insurable_ceiling_caps_the_base_not_the_result(self):
        person = employee(
            regimen="afp", afp="prima", pension_commission_type="flow",
            cuspp="123456ABCDE3", monthly_salary=Decimal("20000.00"),
        )
        entry = self.entry_of(self.run_period(2026, 1), person)
        # Premium over min(20000, 12209.81) × 1.37 % = 167.27.
        self.assertEqual(entry.pension_insurance, Decimal("167.27"))
        # Contribution and commission stay over the full base.
        self.assertEqual(entry.pension_contribution, Decimal("2000.00"))

    def test_income_tax_projection_and_schedule(self):
        """January, 20 000 monthly: hand-computed annual tax 37 575, so
        each of the 12 open months withholds 3 131.25."""
        person = employee(
            regimen="afp", afp="prima", pension_commission_type="flow",
            cuspp="123456ABCDE4", monthly_salary=Decimal("20000.00"),
        )
        entry = self.entry_of(self.run_period(2026, 1), person)

        projection = person.income_tax_projections.get(year=2026)
        # 20000 × 12 + two bonuses of 20000 = 280 000 − 7 UIT (38 500).
        self.assertEqual(projection.projected_annual_income, Decimal("280000.00"))
        self.assertEqual(projection.taxable_income, Decimal("241500.00"))
        self.assertEqual(projection.annual_tax, Decimal("37575.00"))
        # Bracket split must cover the taxable income exactly (V8).
        covered = sum(Decimal(d["amount"]) for d in projection.bracket_detail)
        self.assertEqual(covered, Decimal("241500.00"))
        self.assertEqual(entry.income_tax_withholding, Decimal("3131.25"))

    def test_below_deduction_pays_no_income_tax(self):
        person = employee()  # 3000/month → below 7 UIT after projection
        entry = self.entry_of(self.run_period(), person)
        self.assertEqual(entry.income_tax_withholding, Decimal("0.00"))
        projection = person.income_tax_projections.get(year=2026)
        self.assertEqual(projection.annual_tax, Decimal("0.00"))

    def test_absences_discount_per_spec_formula(self):
        person = employee()
        period = self.run_period()
        entry = self.entry_of(period, person)
        entry.worked_days, entry.absence_days = 26, 4
        entry.save()
        runner.recalculate_entry(entry)
        entry.refresh_from_db()

        codes = lines_by_code(entry)
        self.assertEqual(codes["WORKED_SALARY"].amount, Decimal("2600.00"))
        # Positive amount, DEDUCTION kind: the sign lives in the kind (§4.2).
        self.assertEqual(codes["ABSENCE_DISCOUNT"].amount, Decimal("400.00"))
        self.assertEqual(entry.gross_pay, Decimal("2200.00"))
        self.assertEqual(entry.pension_base, Decimal("2200.00"))

    def test_overtime_split_and_surcharges(self):
        person = employee()  # 3000 → hourly 12.50
        period = self.run_period()
        entry = self.entry_of(period, person)
        entry.first_overtime_hours = Decimal("2")
        entry.additional_overtime_hours = Decimal("3")
        entry.save()
        runner.recalculate_entry(entry)
        entry.refresh_from_db()

        codes = lines_by_code(entry)
        # 12.50 × 1.25 × 2 = 31.25 · 12.50 × 1.35 × 3 = 50.63 (rounded).
        self.assertEqual(codes["OVERTIME_FIRST"].amount, Decimal("31.25"))
        self.assertEqual(codes["OVERTIME_ADDITIONAL"].amount, Decimal("50.63"))
        self.assertEqual(entry.total_hours, Decimal("245.00"))

    def test_health_floor_at_minimum_wage(self):
        """§4.9: half a month below the RMV still contributes over the
        full RMV — the most forgotten rule."""
        person = employee(monthly_salary=Decimal("900.00"))
        entry = self.entry_of(self.run_period(), person)
        self.assertEqual(entry.pension_base, Decimal("900.00"))
        # max(900, 1130) × 9 % = 101.70
        self.assertEqual(entry.health_contribution, Decimal("101.70"))

    def test_mid_month_hire_prorates_by_default(self):
        person = employee(hired_on=datetime.date(2026, 8, 16))
        entry = self.entry_of(self.run_period(), person)
        # 16..31 August = 16 days → 3000 / 30 × 16 = 1600.
        self.assertEqual(entry.worked_days, 16)
        self.assertEqual(entry.gross_pay, Decimal("1600.00"))

    def test_sctr_and_eps_change_the_employer_cost(self):
        person = employee(
            monthly_salary=Decimal("3000.00"), has_eps=True, subject_to_sctr=True,
        )
        entry = self.entry_of(self.run_period(), person)
        # EPS rate 6.75 %: 202.50 · SCTR (0.53 % + 0.68 %) over base: 36.30.
        self.assertEqual(entry.health_contribution, Decimal("202.50"))
        self.assertEqual(entry.work_risk_insurance, Decimal("36.30"))

    def test_sin_regimen_pays_full_with_zero_withholding(self):
        person = employee(regimen="sin_regimen")
        entry = self.entry_of(self.run_period(), person)
        self.assertEqual(entry.pension_contribution, Decimal("0.00"))
        self.assertEqual(entry.net_pay, Decimal("3000.00"))

    def test_missing_salary_is_excluded_not_crashed(self):
        employee(monthly_salary=None)
        period = self.run_period()
        entry = period.entries.first()
        self.assertEqual(entry.gross_pay, Decimal("0.00"))
        incidents = [
            i for i in __import__(
                "payroll.services.validations", fromlist=["validate_period"]
            ).validate_period(period)
            if i.code == "V5"
        ]
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].severity, "error")


class SettledScheduleTests(TenantAPITestCase):
    """§5.4 — settled months never move; only the balance redistributes."""

    def test_raise_after_close_redistributes_only_open_months(self):
        person = employee(
            regimen="afp", afp="prima", pension_commission_type="flow",
            cuspp="123456ABCDE5", monthly_salary=Decimal("20000.00"),
        )
        january = runner.create_period(RUC, 2026, 1)
        runner.calculate_period(january)
        runner.approve_period(january)
        runner.close_period(january, None, accept_warnings=True)

        projection = person.income_tax_projections.get(year=2026)
        january_amount = projection.schedule.get(month=1)
        self.assertTrue(january_amount.is_settled)
        self.assertEqual(january_amount.amount, Decimal("3131.25"))

        # A raise in February recalculates the projection…
        person.monthly_salary = Decimal("25000.00")
        person.save()
        february = runner.create_period(RUC, 2026, 2)
        runner.calculate_period(february)

        projection.refresh_from_db()
        # …but January never moves (the invariant of §5.4).
        self.assertEqual(
            projection.schedule.get(month=1).amount, Decimal("3131.25")
        )
        february_amount = projection.schedule.get(month=2).amount
        self.assertGreater(february_amount, Decimal("3131.25"))
        # Year-end conciliation (V9): schedule covers the annual tax.
        total = sum(row.amount for row in projection.schedule.all())
        self.assertEqual(total, projection.annual_tax)


class StatutoryBonusTests(TenantAPITestCase):
    def test_manual_bonus_gets_the_extraordinary_companion(self):
        from payroll.models import PayrollEntryLine
        from payroll.services.master_data import RatesSnapshot

        person = employee()
        period = runner.create_period(RUC, 2026, 7)
        entry = period.entries.get(colaborador=person)
        rates = RatesSnapshot.resolve(RUC, 2026, 7)
        PayrollEntryLine.objects.create(
            entry=entry, concept=rates.concept("STATUTORY_BONUS"),
            amount=Decimal("3000.00"), is_manual=True,
        )
        runner.recalculate_entry(entry)
        entry.refresh_from_db()

        codes = lines_by_code(entry)
        # Extraordinary bonus at the health rate (9 %): 270.
        self.assertEqual(codes["EXTRAORDINARY_BONUS"].amount, Decimal("270.00"))
        # Statutory bonus is taxable but NOT pensionable (§2.9 note).
        self.assertEqual(entry.pension_base, Decimal("3000.00"))
        self.assertEqual(entry.income_tax_base, Decimal("6270.00"))
        self.assertEqual(entry.gross_pay, Decimal("6270.00"))


class PayslipTests(TenantAPITestCase):
    def test_amount_in_words(self):
        self.assertEqual(
            amount_in_words(Decimal("1234.56")),
            "MIL DOSCIENTOS TREINTA Y CUATRO CON 56/100 SOLES",
        )
        self.assertEqual(amount_in_words(Decimal("0.00")), "CERO CON 00/100 SOLES")

    def test_es_pe_number_format(self):
        self.assertEqual(format_pen(Decimal("1234567.80")), "S/ 1.234.567,80")

    def test_payslip_reprints_byte_identical(self):
        """§8.1: a closed payslip must regenerate exactly, byte by byte."""
        person = employee()
        period = runner.create_period(RUC, 2026, 8)
        entry = period.entries.get(colaborador=person)
        first = render_payslip(entry, "EMPRESA DEMO S.A.C.")
        second = render_payslip(entry, "EMPRESA DEMO S.A.C.")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(b"%PDF"))
