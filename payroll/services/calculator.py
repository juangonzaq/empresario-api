"""Monthly payroll calculator (spec §4). Stateless across employees.

Every amount is rounded to cents when the line is built, not when it is
displayed: the net is a sum of rounded lines, so the payslip squares to the
cent with the bank file and the accounting entry (§4 preamble).

The bases derive from the concept catalog's ``affects_*`` flags (§4.5),
never from hardcoded lists — that is what keeps a payroll from
over-contributing when a new concept appears.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from colaboradores.models import Colaborador, RegimenPensionario

from ..models import ConceptKind, PayrollConcept
from .master_data import RatesSnapshot
from .money import ZERO, D, money


class MissingSalaryError(Exception):
    """V5: an active employee without a salary cannot be calculated."""


@dataclass
class Line:
    concept: PayrollConcept
    amount: Decimal
    quantity: Decimal | None = None
    is_manual: bool = False


@dataclass
class Attendance:
    worked_days: int = 0
    vacation_days: int = 0
    sick_leave_days: int = 0
    leave_days: int = 0
    absence_days: int = 0
    first_overtime_hours: Decimal = ZERO
    additional_overtime_hours: Decimal = ZERO


@dataclass
class PayrollResult:
    lines: list[Line]
    pension_base: Decimal
    income_tax_base: Decimal
    gross_pay: Decimal
    totals: dict = field(default_factory=dict)
    rates_snapshot: dict = field(default_factory=dict)


class PayrollCalculator:
    """Computes one employee's month. ``income_tax_amount`` comes from the
    withholding schedule (§4.7) — it is never derived inside this loop."""

    def __init__(self, rates: RatesSnapshot):
        self.rates = rates
        self.settings = rates.settings

    # ------------------------------------------------------------- public
    def calculate(
        self,
        colaborador: Colaborador,
        attendance: Attendance,
        manual_lines: list[Line],
        income_tax_amount: Decimal,
    ) -> PayrollResult:
        if colaborador.monthly_salary is None:
            raise MissingSalaryError(str(colaborador.pk))

        base = D(colaborador.monthly_salary)
        daily = base / self.settings.days_per_month
        hourly = daily / self.settings.hours_per_day

        lines = self._day_lines(daily, attendance)
        lines += self._overtime_lines(hourly, attendance)
        lines += self._family_allowance(colaborador)
        lines += [line for line in manual_lines if line.amount != 0]
        lines += self._extraordinary_bonus(colaborador, lines)

        pension_base = self._base(lines, "affects_pension_base")
        income_tax_base = self._base(lines, "affects_income_tax_base")
        gross_pay = self._gross(lines)

        lines += self._pension_withholdings(colaborador, pension_base)
        if income_tax_amount > 0:
            lines.append(self._line("INCOME_TAX_WITHHOLDING", income_tax_amount))
        lines += self._employer_costs(colaborador, pension_base)

        totals = self._totals(lines, gross_pay)
        return PayrollResult(
            lines=lines,
            pension_base=pension_base,
            income_tax_base=income_tax_base,
            gross_pay=gross_pay,
            totals=totals,
            rates_snapshot=self._rates_for(colaborador),
        )

    # ------------------------------------------------- earnings by days
    def _day_lines(self, daily: Decimal, att: Attendance) -> list[Line]:
        per_day = [
            ("WORKED_SALARY", att.worked_days),
            ("VACATION", att.vacation_days),
            ("SICK_LEAVE", att.sick_leave_days),
            ("AUTHORIZED_LEAVE", att.leave_days),
            # Positive amount, DEDUCTION kind: the sign lives in the kind,
            # never in the data (§4.2) — the direct path to discounting an
            # absence twice is storing it negative AND subtracting it.
            ("ABSENCE_DISCOUNT", att.absence_days),
        ]
        return [
            self._line(code, daily * days, quantity=D(days))
            for code, days in per_day
            if days
        ]

    def _overtime_lines(self, hourly: Decimal, att: Attendance) -> list[Line]:
        lines = []
        capped = min(
            D(att.first_overtime_hours), D(self.settings.first_overtime_hours)
        )
        if capped > 0:
            lines.append(self._line(
                "OVERTIME_FIRST",
                hourly * (1 + D(self.settings.first_overtime_surcharge)) * capped,
                quantity=capped,
            ))
        additional = D(att.additional_overtime_hours)
        if additional > 0:
            lines.append(self._line(
                "OVERTIME_ADDITIONAL",
                hourly * (1 + D(self.settings.additional_overtime_surcharge))
                * additional,
                quantity=additional,
            ))
        return lines

    def _family_allowance(self, colaborador: Colaborador) -> list[Line]:
        if not colaborador.receives_family_allowance:
            return []
        # Fixed monthly amount: never prorated by worked days (§4.4).
        return [self._line(
            "FAMILY_ALLOWANCE",
            self.rates.minimum_wage * D(self.settings.family_allowance_rate),
        )]

    def _extraordinary_bonus(
        self, colaborador: Colaborador, lines: list[Line]
    ) -> list[Line]:
        """The extraordinary bonus that accompanies the statutory bonus,
        at the SAME rate as the health contribution the worker would have
        generated (§2.6) — which is why the rate lives in SocialHealthRate
        and not in a constant."""
        if any(line.concept.code == "EXTRAORDINARY_BONUS" for line in lines):
            return []  # captured manually: respect the user's figure
        statutory = sum(
            (line.amount for line in lines
             if line.concept.code in ("STATUTORY_BONUS", "TRUNCATED_STATUTORY_BONUS")),
            ZERO,
        )
        if statutory <= 0:
            return []
        health = self.rates.social_health
        rate = health.eps_rate if colaborador.has_eps else health.extraordinary_bonus_rate
        return [self._line("EXTRAORDINARY_BONUS", statutory * D(rate))]

    # ------------------------------------------------------- withholdings
    def _pension_withholdings(
        self, colaborador: Colaborador, pension_base: Decimal
    ) -> list[Line]:
        rate = self.rates.pension_for(colaborador)
        if rate is None:
            # SIN_REGIMEN: the person gets paid, nothing is withheld, and a
            # visible warning is raised by validations (§1.1) — not here.
            return []
        # The ceiling caps the BASE, not the result, and compares against
        # the month's taxable base — overtime can push it past the ceiling
        # even when the contractual salary does not (§4.6).
        insurable = (
            min(pension_base, rate.insurable_ceiling)
            if rate.insurable_ceiling is not None else pension_base
        )
        candidates = [
            ("PENSION_CONTRIBUTION", pension_base * rate.mandatory_contribution_rate),
            ("PENSION_COMMISSION", pension_base * rate.flow_commission_rate),
            ("PENSION_INSURANCE", insurable * rate.insurance_premium_rate),
        ]
        return [self._line(code, amount) for code, amount in candidates if amount > 0]

    # ---------------------------------------------------- employer costs
    def _employer_costs(
        self, colaborador: Colaborador, pension_base: Decimal
    ) -> list[Line]:
        health = self.rates.social_health
        rate = health.eps_rate if colaborador.has_eps else health.standard_rate
        # The minimum-wage floor is the most forgotten rule (§4.9): half a
        # month worked below the RMV still contributes over the full RMV.
        base = (
            max(pension_base, self.rates.minimum_wage)
            if health.applies_minimum_wage_floor else pension_base
        )
        lines = [self._line("HEALTH_CONTRIBUTION", base * D(rate))]
        if colaborador.subject_to_sctr and self.rates.work_risk is not None:
            sctr = self.rates.work_risk
            lines.append(self._line(
                "WORK_RISK_INSURANCE",
                pension_base * (D(sctr.health_rate) + D(sctr.pension_rate)),
            ))
        return lines

    # ------------------------------------------------------------- bases
    def _base(self, lines: list[Line], flag: str) -> Decimal:
        total = Decimal("0")
        for line in lines:
            if not getattr(line.concept, flag):
                continue
            if line.concept.kind == ConceptKind.EARNING:
                total += line.amount
            elif line.concept.kind == ConceptKind.DEDUCTION:
                total -= line.amount
        return money(max(total, Decimal("0")))

    def _gross(self, lines: list[Line]) -> Decimal:
        """Everything paid out, taxable or not (§4.5): earnings minus the
        earning-reducing deductions (absences), before withholdings."""
        total = Decimal("0")
        for line in lines:
            if line.concept.kind == ConceptKind.EARNING:
                total += line.amount
            elif (
                line.concept.kind == ConceptKind.DEDUCTION
                and line.concept.affects_pension_base
            ):
                # Only attendance-style deductions reduce the gross; money
                # deductions (advances, loans) come out of the net instead.
                total -= line.amount
        return money(max(total, Decimal("0")))

    WITHHOLDING_CODES = frozenset({
        "PENSION_CONTRIBUTION", "PENSION_COMMISSION", "PENSION_INSURANCE",
        "INCOME_TAX_WITHHOLDING",
    })

    def _totals(self, lines: list[Line], gross_pay: Decimal) -> dict:
        by_code = {line.concept.code: line.amount for line in lines}
        withholdings = sum(
            (by_code.get(code, ZERO) for code in self.WITHHOLDING_CODES), ZERO
        )
        # Everything the worker owes that is neither a statutory
        # withholding nor an attendance discount already inside the gross:
        # advances, loans, EPS…
        other_deductions = sum(
            (line.amount for line in lines
             if line.concept.kind == ConceptKind.DEDUCTION
             and not line.concept.affects_pension_base
             and line.concept.code not in self.WITHHOLDING_CODES),
            ZERO,
        )
        return {
            "pension_contribution": by_code.get("PENSION_CONTRIBUTION", ZERO),
            "pension_commission": by_code.get("PENSION_COMMISSION", ZERO),
            "pension_insurance": by_code.get("PENSION_INSURANCE", ZERO),
            "income_tax_withholding": by_code.get("INCOME_TAX_WITHHOLDING", ZERO),
            "total_withholdings": money(withholdings),
            "total_deductions": money(other_deductions),
            "net_pay": money(gross_pay - withholdings - other_deductions),
            "health_contribution": by_code.get("HEALTH_CONTRIBUTION", ZERO),
            "work_risk_insurance": by_code.get("WORK_RISK_INSURANCE", ZERO),
            "total_employer_cost": money(
                gross_pay
                + by_code.get("HEALTH_CONTRIBUTION", ZERO)
                + by_code.get("WORK_RISK_INSURANCE", ZERO)
            ),
        }

    def _rates_for(self, colaborador: Colaborador) -> dict:
        rate = self.rates.pension_for(colaborador)
        health = self.rates.social_health
        return {
            "pension": None if rate is None else {
                "fund": rate.pension_fund,
                "commission_type": rate.commission_type,
                "mandatory": str(rate.mandatory_contribution_rate),
                "commission": str(rate.flow_commission_rate),
                "insurance": str(rate.insurance_premium_rate),
                "ceiling": str(rate.insurable_ceiling) if rate.insurable_ceiling else None,
            },
            "health_rate": str(
                health.eps_rate if colaborador.has_eps else health.standard_rate
            ),
            "minimum_wage": str(self.rates.minimum_wage),
        }

    # -------------------------------------------------------------- misc
    def _line(self, code: str, amount, quantity: Decimal | None = None) -> Line:
        return Line(
            concept=self.rates.concept(code),
            amount=money(amount),
            quantity=quantity,
        )
