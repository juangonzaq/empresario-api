"""Financial dashboard models (SPEC_FINANCIAL_DASHBOARD).

Three families:

* **Master tables** (§4) — tax rates, the category tree, the statement
  structure and the ratio catalog. Nothing that depends on a rate, a norm
  or a policy is hardcoded; seed rows are global (``taxpayer_id = ""``)
  and a company may shadow them.
* **The normalization layer** (§3) — ``FinancialTransaction``: every
  source (SUNAT CPE, manual records, payroll) lands in this one table, so
  categorization, statements and ratios share a single code path.
* **Transactional support** (§5) — counterparties consolidated by RUC,
  derived balances, manual balance entries and closing snapshots.

Adaptation decisions over the spec (rule §0.3, reuse before creating):
``DocumentType`` is not duplicated — the CPE module already carries the
document class and the credit-note semantics travel in ``is_credit_note``
plus the category sign. ``TransactionLine`` (§3.1, optional) is deferred:
a transaction carries one category; split allocations come later.
``BankMovement`` is deferred because the ITF source aggregates by month
and bank — there are no individual movements to reconcile yet — so cash
is a manual balance line, clearly marked as such (§5.3).
"""

from __future__ import annotations

from django.conf import settings as django_settings
from django.db import models
from django.db.models import Q

from core.models import BaseModel


class TransactionSource(models.TextChoices):
    SUNAT_SALES = "sunat_sales", "Venta SUNAT"
    SUNAT_PURCHASES = "sunat_purchases", "Compra SUNAT"
    MANUAL_SALE = "manual_sale", "Venta manual"
    MANUAL_PURCHASE = "manual_purchase", "Compra manual"
    PAYROLL = "payroll", "Planilla"
    FEE_RECEIPT = "fee_receipt", "Recibo por honorarios"
    SUNAT_DECLARATION = "sunat_declaration", "Declaración SUNAT"
    SUNAT_PLAME = "sunat_plame", "Planilla declarada (PLAME)"
    SUNAT_ANNUAL = "sunat_annual", "DJ anual de renta"
    ADJUSTMENT = "adjustment", "Ajuste"


class TransactionDirection(models.TextChoices):
    INFLOW = "inflow", "Entrada"
    OUTFLOW = "outflow", "Salida"


class CategorizationStatus(models.TextChoices):
    UNCATEGORIZED = "uncategorized", "Sin categorizar"
    SUGGESTED = "suggested", "Sugerida"
    CONFIRMED = "confirmed", "Confirmada"
    EXCLUDED = "excluded", "Excluida"


class SettlementStatus(models.TextChoices):
    PENDING = "pending", "Pendiente"
    PARTIAL = "partial", "Parcial"
    SETTLED = "settled", "Liquidada"


class StatementKind(models.TextChoices):
    INCOME_STATEMENT = "income_statement", "Estado de Resultados"
    BALANCE_SHEET = "balance_sheet", "Estado de Situación Financiera"


class LineType(models.TextChoices):
    DETAIL = "detail", "Detalle"
    SUBTOTAL = "subtotal", "Subtotal"
    TOTAL = "total", "Total"


class StatementSection(models.TextChoices):
    OPERATING = "operating", "Operativo"
    NON_OPERATING = "non_operating", "No operativo"
    CURRENT_ASSETS = "current_assets", "Activo corriente"
    NON_CURRENT_ASSETS = "non_current_assets", "Activo no corriente"
    CURRENT_LIABILITIES = "current_liabilities", "Pasivo corriente"
    NON_CURRENT_LIABILITIES = "non_current_liabilities", "Pasivo no corriente"
    EQUITY = "equity", "Patrimonio"


class RatioGroup(models.TextChoices):
    LIQUIDITY = "liquidity", "Liquidez"
    MANAGEMENT = "management", "Gestión"
    SOLVENCY = "solvency", "Solvencia"
    PROFITABILITY = "profitability", "Rentabilidad"


class RatioUnit(models.TextChoices):
    TIMES = "times", "Veces"
    DAYS = "days", "Días"
    PERCENT = "percent", "Porcentaje"
    CURRENCY = "currency", "Moneda"


class ThresholdSeverity(models.TextChoices):
    CRITICAL = "critical", "Crítico"
    WARNING = "warning", "Advertencia"
    GOOD = "good", "Óptimo"
    EXCELLENT = "excellent", "Excelente"


class ExpenseClassification(models.TextChoices):
    COST_OF_SALES = "cost_of_sales", "Costo de venta"
    ADMINISTRATIVE = "administrative", "Gastos administrativos"
    SELLING = "selling", "Gastos de ventas"


# ------------------------------------------------------------------ masters
class EffectiveRangeQuerySet(models.QuerySet):
    def effective_on(self, date):
        return self.filter(
            Q(effective_from__lte=date)
            & (Q(effective_to__isnull=True) | Q(effective_to__gte=date))
        )


class TaxRate(BaseModel):
    """§4.2 — IGV and friends, versioned. The (1 + IGV) factor of the
    management ratios reads from here, never from a constant."""

    tax_code = models.CharField("impuesto", max_length=10)
    rate = models.DecimalField("tasa", max_digits=8, decimal_places=6)
    effective_from = models.DateField("vigente desde")
    effective_to = models.DateField("vigente hasta", null=True, blank=True)

    objects = EffectiveRangeQuerySet.as_manager()

    class Meta:
        ordering = ["tax_code", "-effective_from"]
        verbose_name = "tasa de impuesto"
        verbose_name_plural = "tasas de impuesto"

    def __str__(self) -> str:
        return f"{self.tax_code} {self.rate} desde {self.effective_from}"


class ExchangeRate(BaseModel):
    """§4.3 — daily FX, used to convert to the functional currency."""

    currency = models.CharField("moneda", max_length=3)
    rate_date = models.DateField("fecha")
    buy_rate = models.DecimalField(max_digits=12, decimal_places=6)
    sell_rate = models.DecimalField(max_digits=12, decimal_places=6)
    source = models.CharField("fuente", max_length=40, blank=True)

    class Meta:
        ordering = ["-rate_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["currency", "rate_date"], name="unique_fx_per_day"
            )
        ]
        verbose_name = "tipo de cambio"
        verbose_name_plural = "tipos de cambio"

    def __str__(self) -> str:
        return f"{self.currency} {self.rate_date}: {self.sell_rate}"


class StatementLine(BaseModel):
    """§4.5 — the SHAPE of both statements, as data. Subtotals carry a
    formula over other line codes, so restructuring a statement never
    touches the engine."""

    taxpayer_id = models.CharField(
        "RUC", max_length=11, blank=True, db_index=True,
        help_text="Vacío = estructura del catálogo global.",
    )
    statement = models.CharField(max_length=20, choices=StatementKind)
    code = models.CharField("código", max_length=50)
    name = models.CharField("etiqueta", max_length=120)
    line_type = models.CharField(max_length=10, choices=LineType)
    formula = models.CharField(
        "fórmula", max_length=255, blank=True,
        help_text="Para subtotales: NET_SALES + COST_OF_SALES (los DETAIL suman de categorías).",
    )
    section = models.CharField(
        max_length=25, choices=StatementSection, blank=True
    )
    display_order = models.PositiveSmallIntegerField(default=100)
    is_percentage_base = models.BooleanField(
        "base del análisis vertical", default=False
    )

    class Meta:
        ordering = ["statement", "display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "statement", "code"],
                name="unique_statement_line",
            )
        ]
        verbose_name = "línea de estado financiero"
        verbose_name_plural = "líneas de estados financieros"

    def __str__(self) -> str:
        return f"{self.statement}:{self.code}"


class TransactionCategory(BaseModel):
    """§4.4 — the central master: every category knows which statement
    line it feeds and with what sign."""

    taxpayer_id = models.CharField(
        "RUC", max_length=11, blank=True, db_index=True,
        help_text="Vacío = categoría del catálogo global.",
    )
    code = models.CharField("código", max_length=50)
    name = models.CharField("nombre", max_length=120)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="children",
    )
    statement = models.CharField(
        max_length=20, choices=StatementKind, blank=True
    )
    statement_line = models.ForeignKey(
        StatementLine, null=True, blank=True, on_delete=models.PROTECT,
        related_name="categories",
    )
    sign = models.SmallIntegerField(
        default=1, help_text="+1 suma en su renglón, −1 resta."
    )
    applies_to = models.CharField(
        max_length=10, blank=True,
        choices=[("sales", "Ventas"), ("purchases", "Compras"), ("both", "Ambos")],
    )
    is_operating = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "code"], name="unique_category_per_company"
            )
        ]
        verbose_name = "categoría de transacción"
        verbose_name_plural = "categorías de transacción"

    def __str__(self) -> str:
        return f"{self.code}"


class RatioDefinition(BaseModel):
    """§4.6 — ratios as data: formulas over statement-line codes plus the
    IGV_FACTOR and BASE_DAYS variables (§8)."""

    code = models.CharField("código", max_length=40, unique=True)
    name = models.CharField("nombre", max_length=120)
    group = models.CharField(max_length=15, choices=RatioGroup)
    numerator_formula = models.CharField(max_length=255)
    denominator_formula = models.CharField(max_length=255, blank=True)
    multiplier = models.DecimalField(
        max_digits=10, decimal_places=2, default=1,
        help_text="BASE_DAYS en las rotaciones; 1 en el resto.",
    )
    unit = models.CharField(max_length=10, choices=RatioUnit)
    higher_is_better = models.BooleanField(null=True, blank=True)
    recommendation = models.CharField(max_length=200, blank=True)
    interpretation = models.TextField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["group", "display_order"]
        verbose_name = "definición de ratio"
        verbose_name_plural = "definiciones de ratios"

    def __str__(self) -> str:
        return self.code


class RatioThreshold(BaseModel):
    """§4.7 — interpretation ranges are business criteria, so they are
    rows, not ifs. This is what makes the board interpret, not just show."""

    ratio = models.ForeignKey(
        RatioDefinition, related_name="thresholds", on_delete=models.CASCADE
    )
    min_value = models.DecimalField(
        max_digits=14, decimal_places=6, null=True, blank=True
    )
    max_value = models.DecimalField(
        max_digits=14, decimal_places=6, null=True, blank=True
    )
    label = models.CharField(max_length=80)
    severity = models.CharField(max_length=10, choices=ThresholdSeverity)
    advice = models.TextField(blank=True)

    class Meta:
        ordering = ["ratio", "min_value"]
        verbose_name = "umbral de ratio"
        verbose_name_plural = "umbrales de ratios"

    def __str__(self) -> str:
        return f"{self.ratio_id}: {self.label}"


class FinancialSettings(BaseModel):
    """§4.8 — per-company presentation and calculation policy."""

    taxpayer_id = models.CharField("RUC", max_length=11, unique=True)
    functional_currency = models.CharField(max_length=3, default="PEN")
    display_scale = models.CharField(
        max_length=10, default="units",
        choices=[("units", "Unidades"), ("thousands", "Miles"), ("millions", "Millones")],
        help_text="Solo presentación, jamás almacenamiento.",
    )
    days_per_month = models.PositiveSmallIntegerField(default=30)
    days_per_year = models.PositiveSmallIntegerField(default=360)
    use_elapsed_days_for_partial_year = models.BooleanField(default=True)
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)
    minimum_categorized_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=95,
        help_text="Bajo este % el tablero muestra la banda de advertencia (§15).",
    )

    class Meta:
        verbose_name = "configuración financiera"
        verbose_name_plural = "configuraciones financieras"

    def __str__(self) -> str:
        return f"Financials {self.taxpayer_id}"


class FiscalPeriod(BaseModel):
    """§4.10 — a closed month never changes; adjustments enter as new
    transactions in an open period."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=10, default="open",
        choices=[("open", "Abierto"), ("review", "En revisión"), ("closed", "Cerrado")],
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="closed_fiscal_periods",
    )
    settings_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "year", "month"],
                name="unique_fiscal_period",
            )
        ]
        verbose_name = "periodo fiscal"
        verbose_name_plural = "periodos fiscales"

    def __str__(self) -> str:
        return f"{self.taxpayer_id} {self.year}-{self.month:02d} [{self.status}]"


# --------------------------------------------------------------- normalized
class Counterparty(BaseModel):
    """§5.1 — one row per RUC: the same customer written three ways stops
    being three customers. ``default_category`` feeds the categorizer."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    tax_id = models.CharField("RUC de la contraparte", max_length=15, db_index=True)
    legal_name = models.CharField(max_length=255, blank=True)
    kind = models.CharField(
        max_length=10, default="both",
        choices=[("customer", "Cliente"), ("supplier", "Proveedor"), ("both", "Ambos")],
    )
    default_category = models.ForeignKey(
        TransactionCategory, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="default_for",
    )
    credit_days = models.PositiveSmallIntegerField(default=0)
    is_related_party = models.BooleanField(default=False)

    class Meta:
        ordering = ["legal_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "tax_id"], name="unique_counterparty_ruc"
            )
        ]
        verbose_name = "contraparte"
        verbose_name_plural = "contrapartes"

    def __str__(self) -> str:
        return f"{self.tax_id} {self.legal_name}"


class FinancialTransaction(BaseModel):
    """§3 — the single table every source lands in. Amounts are stored
    positive; the sense lives in ``direction`` and ``category.sign``."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    source = models.CharField(max_length=20, choices=TransactionSource)
    source_object_id = models.CharField(max_length=64, blank=True)
    external_id = models.CharField(max_length=120)
    direction = models.CharField(max_length=10, choices=TransactionDirection)
    document_kind = models.CharField(
        "tipo de documento", max_length=20, blank=True,
        help_text="factura / nota_credito / nota_debito / manual / planilla.",
    )
    description = models.CharField("glosa", max_length=255, blank=True)

    issue_date = models.DateField(db_index=True)
    accounting_date = models.DateField("fecha de devengo", db_index=True)
    due_date = models.DateField(null=True, blank=True)

    counterparty_tax_id = models.CharField(max_length=15, blank=True, db_index=True)
    counterparty_name = models.CharField(max_length=255, blank=True)
    counterparty = models.ForeignKey(
        Counterparty, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="transactions",
    )

    currency = models.CharField(max_length=3, default="PEN")
    exchange_rate = models.DecimalField(
        max_digits=12, decimal_places=6, default=1,
        help_text="1 cuando la moneda ya es la funcional.",
    )
    net_amount = models.DecimalField(max_digits=14, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    net_amount_pen = models.DecimalField(max_digits=14, decimal_places=2)
    tax_amount_pen = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount_pen = models.DecimalField(max_digits=14, decimal_places=2)

    category = models.ForeignKey(
        TransactionCategory, null=True, blank=True, on_delete=models.PROTECT,
        related_name="transactions",
    )
    categorization_status = models.CharField(
        max_length=15, choices=CategorizationStatus,
        default=CategorizationStatus.UNCATEGORIZED, db_index=True,
    )
    suggestion_reason = models.CharField(max_length=200, blank=True)
    categorized_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="categorized_transactions",
    )
    categorized_at = models.DateTimeField(null=True, blank=True)

    is_credit_note = models.BooleanField(default=False)
    settlement_status = models.CharField(
        max_length=10, choices=SettlementStatus, default=SettlementStatus.PENDING
    )
    settled_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-accounting_date", "-created_at"]
        indexes = [
            models.Index(fields=["taxpayer_id", "accounting_date"]),
            models.Index(fields=["taxpayer_id", "categorization_status"]),
            models.Index(fields=["taxpayer_id", "counterparty_tax_id"]),
            models.Index(fields=["taxpayer_id", "category", "accounting_date"]),
        ]
        constraints = [
            # Reprocessing a SUNAT download must be idempotent (§3).
            models.UniqueConstraint(
                fields=["taxpayer_id", "source", "external_id"],
                name="unique_transaction_per_source",
            )
        ]
        verbose_name = "transacción financiera"
        verbose_name_plural = "transacciones financieras"

    def __str__(self) -> str:
        return f"[{self.source}] {self.external_id}"


class CategorizationRule(BaseModel):
    """§6.1 — explicit rules, learned from user decisions."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    priority = models.PositiveSmallIntegerField(default=100)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    conditions = models.JSONField(
        help_text='[{"field": "counterparty_tax_id", "op": "equals", "value": "…"}] en AND.'
    )
    category = models.ForeignKey(
        TransactionCategory, on_delete=models.CASCADE, related_name="rules"
    )
    confidence = models.DecimalField(
        max_digits=4, decimal_places=3, default=1,
        help_text="1.0 aplica sin preguntar; menor deja la sugerencia.",
    )
    match_count = models.PositiveIntegerField(default=0)
    last_matched_at = models.DateTimeField(null=True, blank=True)
    created_from_transaction = models.ForeignKey(
        FinancialTransaction, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="rules_created",
    )

    class Meta:
        ordering = ["priority", "created_at"]
        verbose_name = "regla de categorización"
        verbose_name_plural = "reglas de categorización"

    def __str__(self) -> str:
        return self.name


class AccountBalance(BaseModel):
    """§5.2 — derived balance-sheet figures per period. Receivables and
    payables stop being typed and become a consequence of the documents."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    category = models.ForeignKey(
        TransactionCategory, on_delete=models.CASCADE, related_name="balances"
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    source = models.CharField(
        max_length=10, default="derived",
        choices=[("derived", "Derivado"), ("manual", "Manual")],
    )
    computed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "year", "month", "category"],
                name="unique_balance_per_period",
            )
        ]
        verbose_name = "saldo de balance"
        verbose_name_plural = "saldos de balance"

    def __str__(self) -> str:
        return f"{self.category_id} {self.year}-{self.month:02d}: {self.amount}"


class ManualBalanceEntry(BaseModel):
    """§5.3 — lines with no transactional origin (inventory, fixed
    assets, capital). The UI marks them as typed, not guaranteed."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    category = models.ForeignKey(
        TransactionCategory, on_delete=models.CASCADE,
        related_name="manual_balances",
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    notes = models.TextField(blank=True)
    entered_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="balance_entries",
    )

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "year", "month", "category"],
                name="unique_manual_balance",
            )
        ]
        verbose_name = "saldo manual"
        verbose_name_plural = "saldos manuales"

    def __str__(self) -> str:
        return f"{self.category_id} {self.year}-{self.month:02d}: {self.amount}"


class FinancialStatementSnapshot(BaseModel):
    """§5.4 — frozen statement at period close."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    period = models.ForeignKey(
        FiscalPeriod, related_name="snapshots", on_delete=models.CASCADE
    )
    statement = models.CharField(max_length=20, choices=StatementKind)
    lines = models.JSONField(default=dict)
    vertical_analysis = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["period", "statement"], name="unique_snapshot_per_period"
            )
        ]
        verbose_name = "snapshot de estado financiero"
        verbose_name_plural = "snapshots de estados financieros"

    def __str__(self) -> str:
        return f"{self.statement} @ {self.period_id}"
