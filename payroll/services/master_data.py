"""Master-table resolvers (spec §2).

``RatesSnapshot.resolve(...)`` is the single place that turns "a date and a
company" into every legal parameter the engine needs. It raises
``MasterDataMissing`` listing exactly which tables lack a row effective on
that date — that is validation V16, and it blocks a run before it starts
instead of producing a silently wrong payroll.
"""

from __future__ import annotations

import calendar
import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from colaboradores.models import Colaborador, RegimenPensionario

from ..models import (
    ONP_FUND, CommissionType, IncomeTaxBracket, IncomeTaxSettings,
    MinimumWage, PayrollConcept, PayrollSettings, PensionFundRate,
    SocialHealthRate, TaxUnitValue, WorkRiskInsuranceRate,
)


class MasterDataMissing(Exception):
    """V16: a required master table has no row effective on the date."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            "Faltan tablas maestras vigentes: " + ", ".join(missing)
        )


class UnknownConcept(Exception):
    """A concept code was requested that the catalog does not define."""


def period_bounds(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last)


@dataclass
class RatesSnapshot:
    """Every legal parameter resolved for one period, frozen once (§6.1).

    The calculator receives this object and never touches the database for
    rates again — which is also what makes the period snapshot possible.
    """

    year: int
    month: int
    start: datetime.date
    end: datetime.date
    tax_unit: Decimal
    minimum_wage: Decimal
    social_health: SocialHealthRate
    tax_settings: IncomeTaxSettings
    brackets: list[IncomeTaxBracket]
    settings: PayrollSettings
    pension_rates: dict[tuple[str, str], PensionFundRate]
    work_risk: WorkRiskInsuranceRate | None
    concepts: dict[str, PayrollConcept] = field(default_factory=dict)

    # ------------------------------------------------------------ resolve
    @classmethod
    def resolve(cls, taxpayer_id: str, year: int, month: int) -> "RatesSnapshot":
        start, end = period_bounds(year, month)
        missing: list[str] = []

        tax_unit = TaxUnitValue.objects.filter(year=year).first()
        if tax_unit is None:
            missing.append(f"UIT {year}")

        wage = (
            MinimumWage.objects.effective_on(end)
            .order_by("-effective_from")
            .first()
        )
        if wage is None:
            missing.append("remuneración mínima")

        health = (
            SocialHealthRate.objects.effective_on(end)
            .order_by("-effective_from")
            .first()
        )
        if health is None:
            missing.append("tasa de salud")

        tax_settings = IncomeTaxSettings.objects.filter(year=year).first()
        if tax_settings is None:
            missing.append(f"parámetros de renta {year}")

        brackets = list(
            IncomeTaxBracket.objects.filter(year=year).order_by("order")
        )
        if not brackets:
            missing.append(f"tramos de renta {year}")

        pension_rates = {
            (rate.pension_fund, rate.commission_type): rate
            for rate in PensionFundRate.objects.effective_on(end).order_by(
                "effective_from"
            )
        }
        if (ONP_FUND, CommissionType.FLOW) not in pension_rates:
            missing.append("tasa ONP")

        if missing:
            raise MasterDataMissing(missing)

        work_risk = (
            WorkRiskInsuranceRate.objects.effective_on(end)
            .order_by("activity_code", "-effective_from")
            .first()
        )

        settings, _ = PayrollSettings.objects.get_or_create(
            taxpayer_id=taxpayer_id
        )

        # Company concepts shadow global ones by code.
        concepts: dict[str, PayrollConcept] = {}
        for concept in PayrollConcept.objects.filter(
            taxpayer_id__in=["", taxpayer_id], is_active=True
        ).order_by("taxpayer_id"):
            concepts[concept.code] = concept

        return cls(
            year=year, month=month, start=start, end=end,
            tax_unit=tax_unit.amount, minimum_wage=wage.amount,
            social_health=health, tax_settings=tax_settings,
            brackets=brackets, settings=settings,
            pension_rates=pension_rates, work_risk=work_risk,
            concepts=concepts,
        )

    # ------------------------------------------------------------ lookups
    def concept(self, code: str) -> PayrollConcept:
        try:
            return self.concepts[code]
        except KeyError:
            raise UnknownConcept(code)

    def pension_for(self, colaborador: Colaborador) -> PensionFundRate | None:
        """The rate row for this employee, or None when no withholding
        applies (SIN_REGIMEN is a warning, never an error — §1.1)."""
        if colaborador.regimen == RegimenPensionario.ONP:
            return self.pension_rates[(ONP_FUND, CommissionType.FLOW)]
        if colaborador.regimen == RegimenPensionario.AFP:
            commission = colaborador.pension_commission_type or CommissionType.FLOW
            return self.pension_rates.get((colaborador.afp, commission))
        return None

    def as_snapshot(self) -> dict:
        """JSON-safe copy for ``PayrollPeriod.settings_snapshot``."""
        return {
            "tax_unit": str(self.tax_unit),
            "minimum_wage": str(self.minimum_wage),
            "social_health": {
                "standard_rate": str(self.social_health.standard_rate),
                "eps_rate": str(self.social_health.eps_rate),
                "applies_minimum_wage_floor": self.social_health.applies_minimum_wage_floor,
                "extraordinary_bonus_rate": str(self.social_health.extraordinary_bonus_rate),
            },
            "settings": {
                "days_per_month": self.settings.days_per_month,
                "hours_per_day": self.settings.hours_per_day,
                "first_overtime_hours": self.settings.first_overtime_hours,
                "first_overtime_surcharge": str(self.settings.first_overtime_surcharge),
                "additional_overtime_surcharge": str(self.settings.additional_overtime_surcharge),
                "family_allowance_rate": str(self.settings.family_allowance_rate),
                "net_variation_alert_pct": str(self.settings.net_variation_alert_pct),
            },
            "payslip_footer_text": self.settings.payslip_footer_text,
            "pension_rates": {
                f"{fund}/{commission}": {
                    "mandatory": str(rate.mandatory_contribution_rate),
                    "commission": str(rate.flow_commission_rate),
                    "insurance": str(rate.insurance_premium_rate),
                    "ceiling": str(rate.insurable_ceiling) if rate.insurable_ceiling else None,
                }
                for (fund, commission), rate in self.pension_rates.items()
            },
        }
