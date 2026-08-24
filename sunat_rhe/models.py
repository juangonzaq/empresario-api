"""Persistence for the electronic fee receipts (recibos por honorarios)
issued TO the company by independent professionals.

They are fourth-category income for the issuer and a deductible expense
for the company — the gap the CPE scraper leaves open, because SUNAT
serves them from a different SOL application than invoices. They also
carry the 8 % income-tax withholding the company may have to practice,
which is why the withheld amount is a first-class column.
"""

from __future__ import annotations

from django.db import models

from core.models import BaseModel


class FeeReceiptQuerySet(models.QuerySet):
    def for_account(self, account_ruc: str) -> "FeeReceiptQuerySet":
        return self.filter(account_ruc=account_ruc)

    def valid(self) -> "FeeReceiptQuerySet":
        return self.filter(is_reverted=False)

    def for_period(self, period: str) -> "FeeReceiptQuerySet":
        return self.filter(period=period)


class FeeReceipt(BaseModel):
    """One recibo por honorarios electrónico received by the company."""

    account_ruc = models.CharField(
        "RUC de la cuenta", max_length=11, db_index=True,
        help_text="La empresa que recibió el servicio (usuario del recibo).",
    )

    issuer_doc_type = models.CharField("tipo doc. emisor", max_length=2, blank=True)
    issuer_doc = models.CharField(
        "documento del emisor", max_length=15, db_index=True,
        help_text="RUC 10… o DNI del profesional independiente.",
    )
    issuer_name = models.CharField("emisor", max_length=200, blank=True)

    series = models.CharField("serie", max_length=10, blank=True)
    number = models.CharField("número", max_length=20, blank=True)
    full_number = models.CharField("serie-número", max_length=30, blank=True)

    issue_date = models.DateField("fecha de emisión", null=True, blank=True)
    period = models.CharField(
        "periodo", max_length=6, blank=True, db_index=True,
        help_text="aaaamm, derivado de la fecha de emisión.",
    )

    currency = models.CharField("moneda", max_length=3, default="PEN")
    gross_amount = models.DecimalField(
        "importe bruto", max_digits=14, decimal_places=2, null=True, blank=True
    )
    income_tax_withheld = models.DecimalField(
        "retención de renta (8 %)", max_digits=14, decimal_places=2,
        null=True, blank=True,
    )
    net_amount = models.DecimalField(
        "neto pagado", max_digits=14, decimal_places=2, null=True, blank=True
    )

    status = models.CharField("estado", max_length=60, blank=True)
    is_reverted = models.BooleanField(
        "revertido", default=False,
        help_text="Un recibo revertido no es gasto: se lista, no suma.",
    )

    # The receipt's detail page: concept, payment method, observation,
    # clause and the payments list — what the accountant opens the PDF for.
    detail = models.JSONField(default=dict, blank=True)
    detail_fetched_at = models.DateTimeField(null=True, blank=True)

    # Everything scraped, verbatim: new columns surface without re-scraping.
    raw = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    objects = FeeReceiptQuerySet.as_manager()

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["account_ruc", "issuer_doc", "series", "number"],
                name="unique_fee_receipt",
            )
        ]
        indexes = [
            models.Index(fields=["account_ruc", "period"]),
        ]
        verbose_name = "recibo por honorarios"
        verbose_name_plural = "recibos por honorarios"

    def __str__(self) -> str:
        return f"[{self.account_ruc}] {self.full_number} · {self.issuer_name[:30]}"
