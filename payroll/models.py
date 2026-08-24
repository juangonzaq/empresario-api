"""Payroll engine models (Peru, private labour regime).

Two families, per SPEC_PAYROLL_ENGINE.md:

* **Master tables** (§2) — every figure that changes by law lives here,
  versioned by effective-date range, never hardcoded in the calculation
  logic. Legal parameters (UIT, minimum wage, tax brackets, pension rates)
  are national, so they are stored globally; only company policy
  (``PayrollSettings``) and the concept catalog carry ``taxpayer_id``.
* **Transactional models** (§3) — one ``PayrollPeriod`` per company and
  month, one ``PayrollEntry`` per employee, one ``PayrollEntryLine`` per
  concept. A closed period is immutable: payslips reprint byte-identical
  from snapshots even after rates change.

Field names are English; user-facing labels stay Spanish because HR reads
them (spec §0.1).
"""

from __future__ import annotations

from django.conf import settings as django_settings
from django.db import models
from django.db.models import Q

from afpnet.models import Afp
from colaboradores.models import Colaborador
from core.models import BaseModel

# ONP is modelled as one more row of PensionFundRate (spec §2.3), so the
# pension-fund key space is the AFP enum plus "onp" — no second code path.
ONP_FUND = "onp"
PENSION_FUND_CHOICES = [*Afp.choices, (ONP_FUND, "ONP")]


class CommissionType(models.TextChoices):
    FLOW = "flow", "Comisión sobre flujo"
    MIXED = "mixed", "Comisión mixta"


class ConceptKind(models.TextChoices):
    EARNING = "earning", "Ingreso"
    DEDUCTION = "deduction", "Descuento"
    EMPLOYER_COST = "employer_cost", "Aporte del empleador"
    INFORMATIVE = "informative", "Informativo"


class PayrollStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    CALCULATED = "calculated", "Calculado"
    APPROVED = "approved", "Aprobado"
    CLOSED = "closed", "Cerrado"


# --------------------------------------------------------------------------
# Master tables (§2). Global unless noted; resolvers live in
# services/master_data.py so lookups by date happen in exactly one place.
# --------------------------------------------------------------------------


class EffectiveRangeQuerySet(models.QuerySet):
    def effective_on(self, date) -> "EffectiveRangeQuerySet":
        return self.filter(
            Q(effective_from__lte=date)
            & (Q(effective_to__isnull=True) | Q(effective_to__gte=date))
        )


class TaxUnitValue(BaseModel):
    """UIT — Unidad Impositiva Tributaria. One row per fiscal year (§2.1)."""

    year = models.PositiveSmallIntegerField("ejercicio", unique=True)
    amount = models.DecimalField("valor de la UIT", max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-year"]
        verbose_name = "UIT"
        verbose_name_plural = "UIT por ejercicio"

    def __str__(self) -> str:
        return f"UIT {self.year}: {self.amount}"


class MinimumWage(BaseModel):
    """RMV — remuneración mínima vital, versioned by decree (§2.2)."""

    amount = models.DecimalField("remuneración mínima", max_digits=12, decimal_places=2)
    effective_from = models.DateField("vigente desde")
    effective_to = models.DateField("vigente hasta", null=True, blank=True)

    objects = EffectiveRangeQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from"]
        verbose_name = "remuneración mínima"
        verbose_name_plural = "remuneraciones mínimas"

    def __str__(self) -> str:
        return f"RMV {self.amount} desde {self.effective_from}"


class PensionFundRate(BaseModel):
    """Pension system rates per fund and commission type, versioned (§2.3).

    ONP is one more row (``pension_fund = "onp"``): single contribution
    rate, zero commission and premium, no ceiling.
    """

    pension_fund = models.CharField(
        "administradora", max_length=12, choices=PENSION_FUND_CHOICES
    )
    commission_type = models.CharField(
        "tipo de comisión", max_length=10, choices=CommissionType,
        default=CommissionType.FLOW,
    )
    mandatory_contribution_rate = models.DecimalField(
        "aporte obligatorio", max_digits=8, decimal_places=6
    )
    flow_commission_rate = models.DecimalField(
        "comisión sobre flujo", max_digits=8, decimal_places=6, default=0,
        help_text="Cero en comisión mixta: el componente sobre saldo no pasa por planilla.",
    )
    insurance_premium_rate = models.DecimalField(
        "prima de seguro", max_digits=8, decimal_places=6, default=0
    )
    insurable_ceiling = models.DecimalField(
        "tope de remuneración asegurable", max_digits=12, decimal_places=2,
        null=True, blank=True, help_text="Vacío = sin tope (ONP).",
    )
    effective_from = models.DateField("vigente desde")
    effective_to = models.DateField("vigente hasta", null=True, blank=True)

    objects = EffectiveRangeQuerySet.as_manager()

    class Meta:
        ordering = ["pension_fund", "commission_type", "-effective_from"]
        verbose_name = "tasa previsional"
        verbose_name_plural = "tasas previsionales"

    def __str__(self) -> str:
        return f"{self.pension_fund}/{self.commission_type} desde {self.effective_from}"


class IncomeTaxBracket(BaseModel):
    """Progressive fifth-category brackets, widths in UIT (§2.4)."""

    year = models.PositiveSmallIntegerField("ejercicio")
    order = models.PositiveSmallIntegerField("orden")
    width_in_tax_units = models.DecimalField(
        "ancho del tramo (UIT)", max_digits=8, decimal_places=2,
        null=True, blank=True, help_text="Vacío = tramo final sin límite.",
    )
    rate = models.DecimalField("tasa marginal", max_digits=8, decimal_places=6)

    class Meta:
        ordering = ["year", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["year", "order"], name="unique_tax_bracket_per_year"
            )
        ]
        verbose_name = "tramo de renta"
        verbose_name_plural = "tramos de renta"

    def __str__(self) -> str:
        return f"{self.year} tramo {self.order}: {self.rate}"


class IncomeTaxSettings(BaseModel):
    """Yearly fifth-category parameters (§2.5)."""

    year = models.PositiveSmallIntegerField("ejercicio", unique=True)
    standard_deduction_in_tax_units = models.DecimalField(
        "deducción fija (UIT)", max_digits=6, decimal_places=2
    )
    months_in_projection = models.PositiveSmallIntegerField(
        "meses del ejercicio", default=12
    )
    statutory_bonus_count = models.PositiveSmallIntegerField(
        "gratificaciones legales", default=2
    )

    class Meta:
        ordering = ["-year"]
        verbose_name = "parámetros de renta"
        verbose_name_plural = "parámetros de renta por ejercicio"

    def __str__(self) -> str:
        return f"Renta {self.year}"


class SocialHealthRate(BaseModel):
    """Employer health contribution rates, versioned (§2.6)."""

    standard_rate = models.DecimalField("tasa plena", max_digits=8, decimal_places=6)
    eps_rate = models.DecimalField("tasa con EPS", max_digits=8, decimal_places=6)
    applies_minimum_wage_floor = models.BooleanField(
        "base mínima = RMV", default=True
    )
    extraordinary_bonus_rate = models.DecimalField(
        "bonificación extraordinaria", max_digits=8, decimal_places=6,
        help_text="Acompaña a la gratificación; misma tasa que el aporte de salud.",
    )
    effective_from = models.DateField("vigente desde")
    effective_to = models.DateField("vigente hasta", null=True, blank=True)

    objects = EffectiveRangeQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from"]
        verbose_name = "tasa de salud"
        verbose_name_plural = "tasas de salud"

    def __str__(self) -> str:
        return f"Salud {self.standard_rate} desde {self.effective_from}"


class WorkRiskInsuranceRate(BaseModel):
    """SCTR rates per activity risk level, versioned (§2.7)."""

    activity_code = models.CharField("nivel de riesgo", max_length=20)
    health_rate = models.DecimalField("componente salud", max_digits=8, decimal_places=6)
    pension_rate = models.DecimalField("componente pensión", max_digits=8, decimal_places=6)
    effective_from = models.DateField("vigente desde")
    effective_to = models.DateField("vigente hasta", null=True, blank=True)

    objects = EffectiveRangeQuerySet.as_manager()

    class Meta:
        ordering = ["activity_code", "-effective_from"]
        verbose_name = "tasa SCTR"
        verbose_name_plural = "tasas SCTR"

    def __str__(self) -> str:
        return f"SCTR {self.activity_code} desde {self.effective_from}"


class PayrollSettings(BaseModel):
    """Company payroll policy (§2.8). The only per-company master table."""

    taxpayer_id = models.CharField("RUC", max_length=11, unique=True, db_index=True)
    days_per_month = models.PositiveSmallIntegerField("días del mes comercial", default=30)
    hours_per_day = models.PositiveSmallIntegerField("horas de jornada", default=8)
    first_overtime_hours = models.PositiveSmallIntegerField(
        "horas con primer recargo", default=2
    )
    first_overtime_surcharge = models.DecimalField(
        "recargo primeras horas", max_digits=8, decimal_places=6, default="0.25"
    )
    additional_overtime_surcharge = models.DecimalField(
        "recargo horas siguientes", max_digits=8, decimal_places=6, default="0.35"
    )
    family_allowance_rate = models.DecimalField(
        "asignación familiar (proporción de la RMV)", max_digits=8,
        decimal_places=6, default="0.10",
    )
    rounding_mode = models.CharField("modo de redondeo", max_length=10, default="HALF_UP")
    net_variation_alert_pct = models.DecimalField(
        "umbral de variación del neto (%)", max_digits=6, decimal_places=2,
        default="20",
        help_text="Marca en la grilla toda variación de neto mayor a este % contra el mes anterior.",
    )
    payslip_footer_text = models.TextField("pie de la boleta", blank=True)

    class Meta:
        verbose_name = "política de planilla"
        verbose_name_plural = "políticas de planilla"

    def __str__(self) -> str:
        return f"Planilla {self.taxpayer_id}"


class PayrollConcept(BaseModel):
    """Concept catalog (§2.9): rows, not columns, so a new bonus never
    needs a migration. Seed rows are global (``taxpayer_id = ""``); a
    company may add its own, and its rows shadow the global ones by code.

    The two ``affects_*`` flags ARE the math of the bases (§4.5): pension
    and income-tax bases derive from them, never from hardcoded lists.
    """

    taxpayer_id = models.CharField(
        "RUC", max_length=11, blank=True, db_index=True,
        help_text="Vacío = concepto del catálogo global.",
    )
    code = models.CharField("código", max_length=40)
    name = models.CharField("nombre en la boleta", max_length=120)
    kind = models.CharField("tipo", max_length=15, choices=ConceptKind)
    affects_pension_base = models.BooleanField("afecto a aportes", default=False)
    affects_income_tax_base = models.BooleanField("afecto a renta", default=False)
    is_computed = models.BooleanField(
        "calculado por el motor", default=False,
        help_text="Apagado: lo captura el usuario como concepto manual.",
    )
    display_order = models.PositiveSmallIntegerField("orden en boleta", default=100)
    is_active = models.BooleanField("activo", default=True)

    class Meta:
        ordering = ["display_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "code"], name="unique_concept_per_company"
            )
        ]
        verbose_name = "concepto de planilla"
        verbose_name_plural = "conceptos de planilla"

    def __str__(self) -> str:
        return f"{self.code} ({self.get_kind_display()})"


# --------------------------------------------------------------------------
# Transactional models (§3).
# --------------------------------------------------------------------------


class PayrollPeriod(BaseModel):
    """One company payroll month (§3.1). CLOSED means immutable: any later
    fix is a new rectification period, never an edit here."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    year = models.PositiveSmallIntegerField("año")
    month = models.PositiveSmallIntegerField("mes")
    status = models.CharField(
        "estado", max_length=12, choices=PayrollStatus, default=PayrollStatus.DRAFT
    )

    # Snapshots frozen at calculation time (V16 guarantees they exist).
    tax_unit_amount = models.DecimalField(
        "UIT aplicada", max_digits=12, decimal_places=2, null=True, blank=True
    )
    minimum_wage_amount = models.DecimalField(
        "RMV aplicada", max_digits=12, decimal_places=2, null=True, blank=True
    )
    settings_snapshot = models.JSONField("parámetros congelados", default=dict, blank=True)

    calculated_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="closed_payroll_periods",
    )
    # Who accepted the warnings at close time, recorded verbatim (§7.2).
    warnings_accepted_by = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "year", "month"],
                name="unique_payroll_period",
            )
        ]
        verbose_name = "periodo de planilla"
        verbose_name_plural = "periodos de planilla"

    def __str__(self) -> str:
        return f"{self.taxpayer_id} {self.year}-{self.month:02d} [{self.status}]"

    @property
    def is_closed(self) -> bool:
        return self.status == PayrollStatus.CLOSED

    @property
    def is_editable(self) -> bool:
        return self.status in (PayrollStatus.DRAFT, PayrollStatus.CALCULATED)


class PayrollEntry(BaseModel):
    """One employee in one period (§3.2): attendance in, amounts out, plus
    the snapshots that make a closed payslip reproducible."""

    period = models.ForeignKey(
        PayrollPeriod, related_name="entries", on_delete=models.CASCADE
    )
    colaborador = models.ForeignKey(
        Colaborador, related_name="payroll_entries", on_delete=models.PROTECT
    )

    # Snapshots of the employee state used for this month.
    base_salary = models.DecimalField(
        "sueldo base", max_digits=12, decimal_places=2, null=True, blank=True
    )
    pension_regime = models.CharField(max_length=12, blank=True)
    pension_fund = models.CharField(max_length=12, blank=True)
    commission_type = models.CharField(max_length=10, blank=True)
    cuspp = models.CharField(max_length=20, blank=True)

    # Attendance inputs.
    worked_days = models.PositiveSmallIntegerField("días trabajados", default=0)
    vacation_days = models.PositiveSmallIntegerField("días de vacaciones", default=0)
    sick_leave_days = models.PositiveSmallIntegerField("días de descanso médico", default=0)
    leave_days = models.PositiveSmallIntegerField("días de licencia", default=0)
    absence_days = models.PositiveSmallIntegerField("faltas", default=0)
    first_overtime_hours = models.DecimalField(
        "horas extra 25 %", max_digits=6, decimal_places=2, default=0
    )
    additional_overtime_hours = models.DecimalField(
        "horas extra 35 %", max_digits=6, decimal_places=2, default=0
    )

    # Calculated outputs (2-decimal money; §4).
    pension_base = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    income_tax_base = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gross_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pension_contribution = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pension_commission = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pension_insurance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    income_tax_withholding = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_withholdings = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    health_contribution = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    work_risk_insurance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_employer_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    rates_snapshot = models.JSONField("tasas aplicadas", default=dict, blank=True)
    has_warnings = models.BooleanField(default=False)

    class Meta:
        ordering = ["colaborador__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["period", "colaborador"], name="unique_entry_per_period"
            )
        ]
        verbose_name = "línea de planilla"
        verbose_name_plural = "líneas de planilla"

    def __str__(self) -> str:
        return f"{self.colaborador_id} @ {self.period_id}"


class PayrollEntryLine(BaseModel):
    """Per-concept detail (§3.3). The payslip is these rows ordered by
    ``concept.display_order`` — adding a concept never touches the PDF
    template. Amounts are always positive; the sign lives in the kind."""

    entry = models.ForeignKey(
        PayrollEntry, related_name="lines", on_delete=models.CASCADE
    )
    concept = models.ForeignKey(PayrollConcept, on_delete=models.PROTECT)
    quantity = models.DecimalField(
        "cantidad (días u horas)", max_digits=10, decimal_places=2,
        null=True, blank=True,
    )
    amount = models.DecimalField("importe", max_digits=12, decimal_places=2)
    is_manual = models.BooleanField(default=False)

    class Meta:
        ordering = ["concept__display_order"]
        verbose_name = "concepto de la línea"
        verbose_name_plural = "conceptos de la línea"

    def __str__(self) -> str:
        return f"{self.concept_id}: {self.amount}"


class EmployeeSalaryHistory(BaseModel):
    """Salary over time (§3.4). ``Colaborador.monthly_salary`` holds only
    the current value; the annual tax projection needs January's salary in
    August. Written automatically whenever the salary changes."""

    colaborador = models.ForeignKey(
        Colaborador, related_name="salary_history", on_delete=models.CASCADE
    )
    amount = models.DecimalField("sueldo", max_digits=12, decimal_places=2)
    effective_from = models.DateField("vigente desde")
    reason = models.CharField("motivo", max_length=200, blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="salary_changes",
    )

    class Meta:
        ordering = ["-effective_from", "-created_at"]
        verbose_name = "historial de sueldo"
        verbose_name_plural = "historiales de sueldo"

    def __str__(self) -> str:
        return f"{self.colaborador_id}: {self.amount} desde {self.effective_from}"


class IncomeTaxProjection(BaseModel):
    """Annual fifth-category projection per employee and year (§3.5)."""

    colaborador = models.ForeignKey(
        Colaborador, related_name="income_tax_projections", on_delete=models.CASCADE
    )
    year = models.PositiveSmallIntegerField("ejercicio")
    projected_annual_income = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    previous_employer_income = models.DecimalField(
        "ingresos de empleador anterior", max_digits=14, decimal_places=2, default=0
    )
    previous_employer_withheld = models.DecimalField(
        "retenido por empleador anterior", max_digits=14, decimal_places=2, default=0
    )
    profit_sharing = models.DecimalField(
        "participación de utilidades", max_digits=14, decimal_places=2, default=0
    )
    taxable_income = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    annual_tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bracket_detail = models.JSONField(default=list, blank=True)
    # Every component of the projection that produced the withholding, so
    # the accountant can audit the number without recalculating anything.
    computation_detail = models.JSONField(default=dict, blank=True)
    recalculated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year"]
        constraints = [
            models.UniqueConstraint(
                fields=["colaborador", "year"], name="unique_projection_per_year"
            )
        ]
        verbose_name = "proyección de renta"
        verbose_name_plural = "proyecciones de renta"

    def __str__(self) -> str:
        return f"Renta {self.year} · {self.colaborador_id}"


class IncomeTaxWithholdingSchedule(BaseModel):
    """Monthly withholding schedule (§3.6). Settled months are immutable:
    a recalculation only redistributes the pending balance over the open
    months — this is where manual payrolls make their worst mistakes."""

    projection = models.ForeignKey(
        IncomeTaxProjection, related_name="schedule", on_delete=models.CASCADE
    )
    month = models.PositiveSmallIntegerField("mes")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_settled = models.BooleanField(
        default=False, help_text="True cuando el periodo del mes está cerrado."
    )
    # Audited override: the accountant may pin a month's withholding (an
    # inspection adjustment, an agreed retention). The engine then treats
    # the month as fixed and redistributes only the rest. The calculated
    # value stays in ``amount`` so the UI can show both side by side.
    override_amount = models.DecimalField(
        "retención ajustada", max_digits=12, decimal_places=2,
        null=True, blank=True,
    )
    override_reason = models.CharField("motivo del ajuste", max_length=200, blank=True)
    overridden_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="income_tax_overrides",
    )
    overridden_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["month"]
        constraints = [
            models.UniqueConstraint(
                fields=["projection", "month"], name="unique_schedule_month"
            )
        ]
        verbose_name = "retención programada"
        verbose_name_plural = "cronograma de retenciones"

    @property
    def effective_amount(self):
        """What the payslip actually reads: the override when present."""
        return self.amount if self.override_amount is None else self.override_amount

    def __str__(self) -> str:
        return f"mes {self.month}: {self.amount}"


class IncomeTaxMonthlyInput(BaseModel):
    """Actual taxable income and withholding for months the engine did not
    run: the initial load when the system starts mid-year. The projection
    reads these for months without a CLOSED period; once a month has a
    closed period, the engine's own record wins and the input is ignored.
    Without this data a mid-year start projects roughly half the income
    and withholds far too little — the exact bug this table closes."""

    colaborador = models.ForeignKey(
        Colaborador, related_name="income_tax_monthly_inputs",
        on_delete=models.CASCADE,
    )
    year = models.PositiveSmallIntegerField("ejercicio")
    month = models.PositiveSmallIntegerField("mes")
    taxable_income = models.DecimalField(
        "ingreso afecto real", max_digits=12, decimal_places=2, default=0
    )
    withheld = models.DecimalField(
        "retención practicada", max_digits=12, decimal_places=2, default=0
    )
    note = models.CharField("nota", max_length=200, blank=True)

    class Meta:
        ordering = ["year", "month"]
        constraints = [
            models.UniqueConstraint(
                fields=["colaborador", "year", "month"],
                name="unique_monthly_input",
            )
        ]
        verbose_name = "histórico mensual de renta"
        verbose_name_plural = "históricos mensuales de renta"

    def __str__(self) -> str:
        return f"{self.year}-{self.month:02d}: {self.taxable_income}"
