"""Source adapters (spec §2–§3): SUNAT CPE, manual records and payroll
land as ``FinancialTransaction`` rows. Idempotent by ``(source,
external_id)`` — reprocessing a download updates, never duplicates.

The information is NOT duplicated conceptually: these rows are the
normalization layer the statements read; the raw sources stay authoritative
for audit, exactly like ``finance_analytics`` treats them.
"""

from __future__ import annotations

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from colaboradores.models import Colaborador
from finance_analytics.models import ManualEntry
from payroll.models import PayrollEntry, PayrollStatus
from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

from ..models import (
    Counterparty, ExchangeRate, FinancialTransaction, TaxRate,
    TransactionDirection, TransactionSource,
)

CENTS = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def igv_rate_on(date: datetime.date) -> Decimal:
    row = (
        TaxRate.objects.filter(tax_code="IGV").effective_on(date)
        .order_by("-effective_from").first()
    )
    return row.rate if row else Decimal("0")


def fx_rate_on(currency: str, date: datetime.date) -> Decimal | None:
    row = (
        ExchangeRate.objects.filter(currency=currency, rate_date__lte=date)
        .order_by("-rate_date")
        .first()
    )
    return row.sell_rate if row else None


def _resolve_counterparty(
    taxpayer_id: str, tax_id: str, name: str
) -> Counterparty | None:
    """Consolidation by RUC (§5.1): three spellings, one counterparty."""
    if not tax_id:
        return None
    counterparty, created = Counterparty.objects.get_or_create(
        taxpayer_id=taxpayer_id, tax_id=tax_id,
        defaults={"legal_name": name or ""},
    )
    if not created and name and not counterparty.legal_name:
        counterparty.legal_name = name
        counterparty.save(update_fields=["legal_name", "updated_at"])
    return counterparty


def _upsert(taxpayer_id: str, source: str, external_id: str, fields: dict) -> bool:
    """True when created. Confirmed categorizations are never overwritten
    by a re-ingest: the human decision survives the sync, with the same
    criterion the rest of the product applies to manual data."""
    existing = FinancialTransaction.objects.filter(
        taxpayer_id=taxpayer_id, source=source, external_id=external_id
    ).first()
    if existing is None:
        FinancialTransaction.objects.create(
            taxpayer_id=taxpayer_id, source=source, external_id=external_id,
            **fields,
        )
        return True
    protected = {"category", "categorization_status", "categorized_by",
                 "categorized_at", "suggestion_reason", "settlement_status",
                 "settled_amount"}
    for name, value in fields.items():
        if name not in protected:
            setattr(existing, name, value)
    existing.save()
    return False


def _amounts(net: Decimal, tax: Decimal, currency: str, rate: Decimal) -> dict:
    total = net + tax
    return {
        "currency": currency,
        "exchange_rate": rate,
        "net_amount": money(net),
        "tax_amount": money(tax),
        "total_amount": money(total),
        "net_amount_pen": money(net * rate),
        "tax_amount_pen": money(tax * rate),
        "total_amount_pen": money(total * rate),
    }


# ------------------------------------------------------------------- CPE
def _invoice_net_and_tax(invoice: ElectronicInvoice, igv: Decimal) -> tuple[Decimal, Decimal]:
    extract = getattr(invoice, "extract", None)
    total = invoice.total_amount
    if extract and extract.total_amount is not None:
        total = extract.total_amount if total is None else total
    if total is None:
        total = Decimal("0")
    if extract and extract.taxable_amount is not None and extract.igv_amount is not None:
        return extract.taxable_amount, extract.igv_amount
    # No XML detail: derive the base with the master IGV rate of the date.
    net = total / (1 + igv) if igv else total
    return money(net), money(total - net)


def ingest_sunat(taxpayer_id: str) -> dict:
    created = updated = 0
    invoices = (
        ElectronicInvoice.objects.for_account(taxpayer_id)
        .select_related("extract")
        .defer("xml_content", "raw")
    )
    for invoice in invoices:
        if invoice.is_cancelled or invoice.is_rejected:
            continue
        issued = invoice.direction == Direction.ISSUED
        date = invoice.issue_date or datetime.date(
            int(invoice.period[:4]), int(invoice.period[4:]), 1
        )
        igv = igv_rate_on(date)
        net, tax = _invoice_net_and_tax(invoice, igv)
        rate = Decimal("1")
        currency = invoice.currency or "PEN"
        if currency != "PEN":
            rate = fx_rate_on(currency, date) or Decimal("1")
        counterparty_ruc = invoice.receiver_ruc if issued else invoice.issuer_ruc
        counterparty_name = invoice.receiver_name if issued else invoice.issuer_name

        fields = {
            "source_object_id": str(invoice.pk),
            "direction": (
                TransactionDirection.INFLOW if issued
                else TransactionDirection.OUTFLOW
            ),
            "document_kind": invoice.document_class,
            "description": invoice.full_number or f"{invoice.series}-{invoice.number}",
            "issue_date": date,
            "accounting_date": date,
            "counterparty_tax_id": counterparty_ruc or "",
            "counterparty_name": counterparty_name or "",
            "counterparty": _resolve_counterparty(
                taxpayer_id, counterparty_ruc or "", counterparty_name or ""
            ),
            "is_credit_note": invoice.document_class == DocumentClass.CREDIT_NOTE,
            "metadata": {"period": invoice.period},
            **_amounts(net, tax, currency, rate),
        }
        source = (
            TransactionSource.SUNAT_SALES if issued
            else TransactionSource.SUNAT_PURCHASES
        )
        external_id = f"{invoice.issuer_ruc}-{invoice.document_type}-{invoice.series}-{invoice.number}"
        if _upsert(taxpayer_id, source, external_id, fields):
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


# ---------------------------------------------------------------- manual
def ingest_manual(taxpayer_id: str) -> dict:
    created = updated = 0
    for entry in ManualEntry.objects.filter(account_ruc=taxpayer_id):
        issued = entry.direction == Direction.ISSUED
        rate = Decimal("1")
        currency = entry.currency or "PEN"
        if currency != "PEN":
            rate = fx_rate_on(currency, entry.entry_date) or Decimal("1")
        fields = {
            "source_object_id": str(entry.pk),
            "direction": (
                TransactionDirection.INFLOW if issued
                else TransactionDirection.OUTFLOW
            ),
            "document_kind": "manual",
            "description": entry.description,
            "issue_date": entry.entry_date,
            "accounting_date": entry.entry_date,
            "counterparty_tax_id": "",
            "counterparty_name": entry.counterparty,
            # Manual records carry no tax breakdown: net = total, tax = 0.
            **_amounts(entry.amount, Decimal("0"), currency, rate),
        }
        source = (
            TransactionSource.MANUAL_SALE if issued
            else TransactionSource.MANUAL_PURCHASE
        )
        if _upsert(taxpayer_id, source, str(entry.pk), fields):
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


# --------------------------------------------------------------- payroll
PAYROLL_CATEGORY_BY_CLASSIFICATION = {
    "cost_of_sales": "PAYROLL_COST_OF_SALES",
    "administrative": "PAYROLL_ADMIN",
    "selling": "PAYROLL_SELLING",
}


def ingest_payroll(taxpayer_id: str) -> dict:
    """§6.4 — closed payroll periods become CONFIRMED transactions, one
    per employee, categorized by ``Colaborador.expense_classification``:
    the labour cost of the income statement is traceable to the person."""
    from ..models import CategorizationStatus, TransactionCategory

    created = updated = 0
    entries = PayrollEntry.objects.filter(
        period__taxpayer_id=taxpayer_id,
        period__status=PayrollStatus.CLOSED,
    ).select_related("period", "colaborador")
    categories = {
        c.code: c
        for c in TransactionCategory.objects.filter(
            taxpayer_id__in=["", taxpayer_id],
            code__in=PAYROLL_CATEGORY_BY_CLASSIFICATION.values(),
        )
    }
    for entry in entries:
        if entry.total_employer_cost <= 0:
            continue
        period = entry.period
        date = datetime.date(period.year, period.month, 1)
        code = PAYROLL_CATEGORY_BY_CLASSIFICATION.get(
            entry.colaborador.expense_classification, "PAYROLL_ADMIN"
        )
        fields = {
            "source_object_id": str(entry.pk),
            "direction": TransactionDirection.OUTFLOW,
            "document_kind": "planilla",
            "description": f"Planilla {period.year}-{period.month:02d} · "
                           f"{entry.colaborador.full_name}",
            "issue_date": date,
            "accounting_date": date,
            "counterparty_tax_id": "",
            "counterparty_name": entry.colaborador.full_name,
            "category": categories.get(code),
            "categorization_status": CategorizationStatus.CONFIRMED,
            "categorized_at": timezone.now(),
            "settlement_status": "settled",
            **_amounts(entry.total_employer_cost, Decimal("0"), "PEN", Decimal("1")),
        }
        if _upsert(taxpayer_id, TransactionSource.PAYROLL, str(entry.pk), fields):
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


def ingest_all(taxpayer_id: str) -> dict:
    return {
        "sunat": ingest_sunat(taxpayer_id),
        "manual": ingest_manual(taxpayer_id),
        "payroll": ingest_payroll(taxpayer_id),
    }
