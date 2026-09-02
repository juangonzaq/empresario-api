"""Data model for the tax & financial reconciliation engine.

Everything the deterministic engine concludes is persisted here, per company
and tax period, so the dashboard can show history and evolution. Sources are
the models that already exist in the project — ``sunat_cpe.ElectronicInvoice``
(CPE), ``sensor_sunat.SalesDoc``/``PurchaseDoc``/``BoxSnapshot`` (SIRE) and
``sunat_itf.ItfRecord`` — plus the bank movements stored here.

Ground rule (encoded in labels and copy, not only in prose): a difference is
NEVER presented as evasion or omission. It is an *inconsistency* or a
*movement pending review*, with possible explanations attached.
"""

from __future__ import annotations

from django.db import models

from django.contrib.auth import get_user_model

from core.models import BaseModel

User = get_user_model()


class MatchLevel(models.TextChoices):
    """Outcome classification for one reconciled item, per spec."""

    OK = "ok", "OK"
    WARNING = "warning", "Advertencia"
    REVIEW = "review", "Revisar"
    CRITICAL = "critical", "Crítico"


class RunStatus(models.TextChoices):
    RUNNING = "running", "Ejecutando"
    DONE = "done", "Terminado"
    FAILED = "failed", "Falló"


class ReconciliationRun(BaseModel):
    """One execution of the engine for a company and period. Keeps the raw
    aggregate numbers so the dashboard reads one row, not five sources."""

    account_ruc = models.CharField(max_length=11, db_index=True)
    period = models.CharField(max_length=6, db_index=True)
    status = models.CharField(max_length=10, choices=RunStatus, default=RunStatus.RUNNING)
    error = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # {"sales_cpe": .., "sales_sire": .., "sales_declared": .., "purchases_*": ..,
    #  "itf_total": .., "bank_credits": .., "igv_declared": .., counters ...}
    totals = models.JSONField(default=dict, blank=True)
    findings_count = models.JSONField(default=dict, blank=True)  # {"warning": 3, ...}
    # Prose from the AI layer (summary + priorities); deterministic data above.
    ai_explanation = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account_ruc", "period"])]

    def __str__(self) -> str:
        return f"{self.account_ruc} {self.period} · {self.status}"


class DocDirection(models.TextChoices):
    SALES = "sales", "Ventas"
    PURCHASES = "purchases", "Compras"


class DocMatchStatus(models.TextChoices):
    MATCHED = "matched", "Coincide"
    CPE_ONLY = "cpe_only", "CPE sin registro SIRE"
    SIRE_ONLY = "sire_only", "Registro SIRE sin CPE"
    AMOUNT_MISMATCH = "amount_mismatch", "Diferencia de monto"
    IGV_MISMATCH = "igv_mismatch", "Diferencia de IGV"
    DATE_MISMATCH = "date_mismatch", "Diferencia de fecha"
    PARTY_MISMATCH = "party_mismatch", "Diferencia de contraparte"
    CANCELLED_MISMATCH = "cancelled_mismatch", "Anulado registrado distinto"


class DocumentReconciliation(BaseModel):
    """The CPE↔SIRE verdict for one document key within a period."""

    run = models.ForeignKey(ReconciliationRun, related_name="documents", on_delete=models.CASCADE)
    account_ruc = models.CharField(max_length=11, db_index=True)
    period = models.CharField(max_length=6, db_index=True)
    direction = models.CharField(max_length=10, choices=DocDirection)
    # Normalized key: "<doc_type>-<series>-<number>" (zero-stripped number).
    doc_key = models.CharField(max_length=40, db_index=True)
    counterparty_ruc = models.CharField(max_length=15, blank=True)
    counterparty_name = models.CharField(max_length=200, blank=True)
    cpe = models.ForeignKey(
        "sunat_cpe.ElectronicInvoice", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reconciliations",
    )
    sire_id = models.IntegerField(null=True, blank=True)  # SalesDoc/PurchaseDoc pk
    status = models.CharField(max_length=20, choices=DocMatchStatus)
    level = models.CharField(max_length=10, choices=MatchLevel)
    cpe_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    sire_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cpe_igv = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    sire_igv = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    # {"amount_diff": .., "igv_diff": .., "date_diff_days": .., "notes": [..]}
    differences = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["level", "doc_key"]
        indexes = [models.Index(fields=["account_ruc", "period", "direction"])]

    def __str__(self) -> str:
        return f"{self.doc_key} · {self.status}"


class DeclaredSummary(BaseModel):
    """What the company actually declared for a period (form 621 figures).

    Filled from the best available source, in this order: SIRE casillas
    comparison when SUNAT exposes it, a manual entry from the accountant, or
    an import. ``source`` says which, because the cross-check against SIRE is
    only as strong as this row."""

    class Source(models.TextChoices):
        SIRE = "sire", "Reporte de casillas SIRE"
        MANUAL = "manual", "Ingresado a mano"
        IMPORT = "import", "Importado"

    account_ruc = models.CharField(max_length=11, db_index=True)
    period = models.CharField(max_length=6, db_index=True)
    sales_base = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    sales_igv = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    purchases_base = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    purchases_igv = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    igv_payable = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    income_tax_declared = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    total_declared = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    filed_at = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=Source, default=Source.MANUAL)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("account_ruc", "period")
        ordering = ["-period"]

    def __str__(self) -> str:
        return f"{self.account_ruc} {self.period} declarado"


class MovementKind(models.TextChoices):
    CREDIT = "credit", "Abono"
    DEBIT = "debit", "Cargo"


class MovementCategory(models.TextChoices):
    INVOICE_COLLECTION = "invoice_collection", "Cobro de factura"
    SUPPLIER_PAYMENT = "supplier_payment", "Pago a proveedor"
    OWN_ACCOUNT_TRANSFER = "own_transfer", "Transferencia entre cuentas propias"
    LOAN = "loan", "Préstamo"
    CAPITAL_CONTRIBUTION = "capital", "Aporte de capital"
    REFUND = "refund", "Devolución"
    REIMBURSEMENT = "reimbursement", "Reembolso"
    TAX_PAYMENT = "tax_payment", "Pago de tributos"
    PAYROLL_PAYMENT = "payroll", "Pago de planilla"
    OTHER = "other", "Otros"
    UNIDENTIFIED = "unidentified", "No identificado"


class BankMovement(BaseModel):
    """One bank movement, imported or typed in. The classifier fills the
    category with its confidence and evidence; a person can always override
    (``classified_by = user`` wins and is never recomputed)."""

    class ClassifiedBy(models.TextChoices):
        RULES = "rules", "Reglas"
        AI = "ai", "IA (auxiliar)"
        USER = "user", "Usuario"

    account_ruc = models.CharField(max_length=11, db_index=True)
    date = models.DateField()
    period = models.CharField(max_length=6, db_index=True)  # derived from date
    bank = models.CharField(max_length=60, blank=True)
    bank_account = models.CharField(max_length=40, blank=True)
    currency = models.CharField(max_length=3, default="PEN")
    kind = models.CharField(max_length=10, choices=MovementKind)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=300, blank=True)
    operation_number = models.CharField(max_length=40, blank=True)
    source = models.CharField(max_length=20, default="import")  # import | manual | statement
    statement = models.ForeignKey(
        "reconciliation.BankStatement", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="movements",
    )
    category = models.CharField(max_length=20, choices=MovementCategory, default=MovementCategory.UNIDENTIFIED)
    confidence = models.FloatField(null=True, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    classified_by = models.CharField(max_length=10, choices=ClassifiedBy, blank=True)
    # Auditoría de la clasificación: QUIÉN decidió y CUÁNDO exactamente. La
    # columna de evidencia lo muestra; sin esto, «Clasificado por el usuario»
    # no decía cuál usuario ni de qué fecha.
    classified_at = models.DateTimeField(null=True, blank=True)
    classified_by_user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["-date"]
        indexes = [models.Index(fields=["account_ruc", "period", "kind"])]

    def __str__(self) -> str:
        return f"{self.date} {self.kind} {self.currency} {self.amount}"


class SettlementStatus(models.TextChoices):
    UNPAID = "unpaid", "Sin pago"
    PARTIAL = "partial", "Pago parcial"
    PAID = "paid", "Pagada"
    # Sin abono bancario pero con nota(s) de crédito que cubren el total: no
    # hay nada que cobrar, y llamarla «pagada» mentiría sobre la caja.
    CREDITED = "credited", "Cubierta por nota de crédito"
    OVERPAID = "overpaid", "Pago excedente"
    UNDETERMINED = "undetermined", "No determinado"


class InvoiceSettlement(BaseModel):
    """Collection state of one invoice: which movements pay it and how much
    remains. Supports 1→N, N→1 and partial payments via ``SettlementLine``.
    Keeps the billing period separate from the collection period on purpose:
    a June invoice collected in July must never be compared month-to-month."""

    account_ruc = models.CharField(max_length=11, db_index=True)
    invoice = models.OneToOneField(
        "sunat_cpe.ElectronicInvoice", related_name="settlement", on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=15, choices=SettlementStatus, default=SettlementStatus.UNPAID)
    invoice_total = models.DecimalField(max_digits=14, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Notas de crédito emitidas que referencian esta factura: reducen lo
    # cobrable. balance = total − NC − pagado.
    credit_notes_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    billing_period = models.CharField(max_length=6, db_index=True)
    collection_period = models.CharField(max_length=6, blank=True)  # of the last payment
    last_payment_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-billing_period"]

    def __str__(self) -> str:
        return f"{self.invoice_id} · {self.status}"


class SettlementLine(BaseModel):
    settlement = models.ForeignKey(InvoiceSettlement, related_name="lines", on_delete=models.CASCADE)
    movement = models.ForeignKey(BankMovement, related_name="settlement_lines", on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    confidence = models.FloatField(default=0)
    evidence = models.JSONField(default=list, blank=True)
    matched_by = models.CharField(max_length=10, default="rules")  # rules | user

    class Meta:
        unique_together = ("settlement", "movement")


class ConsistencyScore(BaseModel):
    """The 0–100 tax-consistency indicator for a period, with its breakdown
    so the number can always be explained."""

    account_ruc = models.CharField(max_length=11, db_index=True)
    period = models.CharField(max_length=6, db_index=True)
    run = models.ForeignKey(ReconciliationRun, null=True, blank=True, on_delete=models.SET_NULL)
    score = models.PositiveSmallIntegerField()
    # [{"factor": "...", "penalty": 8, "detail": "..."}]
    breakdown = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = ("account_ruc", "period")
        ordering = ["-period"]

    def __str__(self) -> str:
        return f"{self.account_ruc} {self.period}: {self.score}"


def score_band(score: int) -> str:
    if score >= 92: return "excellent"
    if score >= 80: return "good"
    if score >= 60: return "review"
    if score >= 40: return "risk"
    return "critical"


class StatementStatus(models.TextChoices):
    UPLOADED = "uploaded", "Subido"
    LOCKED = "locked", "Protegido (falta contraseña)"
    PARSED = "parsed", "Procesado"
    FAILED = "failed", "Falló"


class BankStatement(BaseModel):
    """An uploaded bank account statement PDF, per currency.

    Statements are usually password-protected. The default password follows the
    bank convention the user configured: the 8 RUC digits after the 2nd and
    without the last (``RUC[2:10]``). A different password can be supplied per
    upload; it is used to open the file and never stored.
    """

    account_ruc = models.CharField(max_length=11, db_index=True)
    file = models.FileField(upload_to="statements/%Y/%m/")
    original_name = models.CharField(max_length=255, blank=True)
    bank = models.CharField(max_length=60, blank=True)
    bank_account = models.CharField(max_length=40, blank=True)
    currency = models.CharField(max_length=3, default="PEN")
    status = models.CharField(max_length=10, choices=StatementStatus, default=StatementStatus.UPLOADED)
    error = models.TextField(blank=True)
    movement_count = models.PositiveIntegerField(default=0)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.account_ruc} · {self.bank or 'banco'} {self.currency}"
