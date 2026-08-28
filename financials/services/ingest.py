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

from django.db.models import Sum
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
def _manual_source_and_fields(entry: ManualEntry) -> tuple[str, dict]:
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
    return source, fields


def _apply_manual_category(entry: ManualEntry, *, force: bool) -> None:
    """Carry the category coded on the manual record into its transaction.

    ``force`` distinguishes the two callers: editing the record itself is
    authoritative (the accountant just coded the voucher), while a bulk
    re-sync must not clobber a decision taken later in Categorizar."""
    from ..models import CategorizationStatus, TransactionCategory

    source, _ = _manual_source_and_fields(entry)
    transaction = FinancialTransaction.objects.filter(
        taxpayer_id=entry.account_ruc, source=source, external_id=str(entry.pk)
    ).first()
    if transaction is None:
        return
    if not force and transaction.categorization_status == CategorizationStatus.CONFIRMED:
        return

    category = None
    if entry.category_code:
        # The company's own row overrides the global catalog on same code.
        category = (
            TransactionCategory.objects.filter(
                taxpayer_id__in=["", entry.account_ruc],
                code=entry.category_code, is_active=True,
            ).order_by("-taxpayer_id").first()
        )
    if category is not None:
        transaction.category = category
        transaction.categorization_status = CategorizationStatus.CONFIRMED
        transaction.categorized_at = timezone.now()
    elif force and not entry.category_code:
        # The accountant removed the code: back to the Categorizar queue.
        transaction.category = None
        transaction.categorization_status = CategorizationStatus.UNCATEGORIZED
        transaction.categorized_at = None
    transaction.save(update_fields=[
        "category", "categorization_status", "categorized_at", "updated_at",
    ])


def sync_manual_entry(entry: ManualEntry) -> None:
    """One record, right now: saving a manual income or expense must show
    up in the income statement without waiting for the sync button."""
    source, fields = _manual_source_and_fields(entry)
    _upsert(entry.account_ruc, source, str(entry.pk), fields)
    _apply_manual_category(entry, force=True)


def remove_manual_entry(account_ruc: str, entry_pk: str) -> None:
    """Deleting the record deletes its transaction: an orphan row would
    keep feeding the income statement with a document that no longer
    exists."""
    FinancialTransaction.objects.filter(
        taxpayer_id=account_ruc,
        source__in=[
            TransactionSource.MANUAL_SALE, TransactionSource.MANUAL_PURCHASE,
        ],
        external_id=entry_pk,
    ).delete()


def ingest_manual(taxpayer_id: str) -> dict:
    created = updated = 0
    for entry in ManualEntry.objects.filter(account_ruc=taxpayer_id):
        source, fields = _manual_source_and_fields(entry)
        if _upsert(taxpayer_id, source, str(entry.pk), fields):
            created += 1
        else:
            updated += 1
        _apply_manual_category(entry, force=False)
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


# ----------------------------------------------------------- fee receipts
FEE_RECEIPT_CATEGORY = "PROFESSIONAL_FEES"


def ingest_fee_receipts(taxpayer_id: str) -> dict:
    """Fee receipts received (recibos por honorarios) become CONFIRMED
    expense transactions under «Honorarios profesionales» — the accountant
    can recategorize one in Categorizar and the re-sync respects it, same
    contract as payroll."""
    from sunat_rhe.models import FeeReceipt

    from ..models import CategorizationStatus, TransactionCategory

    category = TransactionCategory.objects.filter(
        taxpayer_id__in=["", taxpayer_id],
        code=FEE_RECEIPT_CATEGORY, is_active=True,
    ).order_by("-taxpayer_id").first()

    created = updated = 0
    receipts = FeeReceipt.objects.for_account(taxpayer_id).valid()
    for receipt in receipts:
        if not receipt.gross_amount or receipt.gross_amount <= 0:
            continue
        rate = Decimal("1")
        currency = receipt.currency or "PEN"
        if currency != "PEN" and receipt.issue_date:
            rate = fx_rate_on(currency, receipt.issue_date) or Decimal("1")
        fields = {
            "source_object_id": str(receipt.pk),
            "direction": TransactionDirection.OUTFLOW,
            "document_kind": "recibo_honorarios",
            "description": f"RHE {receipt.full_number} · {receipt.issuer_name}",
            "issue_date": receipt.issue_date,
            "accounting_date": receipt.issue_date,
            "counterparty_tax_id": receipt.issuer_doc,
            "counterparty_name": receipt.issuer_name,
            "category": category,
            "categorization_status": CategorizationStatus.CONFIRMED,
            "categorized_at": timezone.now(),
            # The expense is the GROSS fee: the 8 % withheld is part of the
            # cost — it goes to SUNAT instead of the professional.
            **_amounts(receipt.gross_amount, Decimal("0"), currency, rate),
        }
        if _upsert(
            taxpayer_id, TransactionSource.FEE_RECEIPT, str(receipt.pk), fields
        ):
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


# ------------------------------------------------------ declaraciones 621
INCOME_TAX_CATEGORY = "INCOME_TAX"


def ingest_declarations(taxpayer_id: str) -> dict:
    """El pago a cuenta de renta **declarado** en el F.V. 621 (casilla 312) es
    el impuesto a la renta del mes en el Estado de Resultados: una transacción
    CONFIRMADA por periodo, con fuente propia y trazable al número de orden.

    Se toma el 621 vigente de cada periodo (la rectificatoria más reciente); si
    una rectificatoria cambia la cifra, la misma transacción se actualiza. El
    IGV no entra: es un pasivo, no un gasto. Un 621 sin pago a cuenta (S/ 0)
    no genera fila, y si dejara de haber pago a cuenta la fila se retira.
    """
    from sunat_declaraciones.services.casillas import resumen_621
    from sunat_declaraciones.services.sync import vigentes_621

    from ..models import CategorizationStatus, TransactionCategory

    category = (
        TransactionCategory.objects.filter(
            taxpayer_id__in=["", taxpayer_id], code=INCOME_TAX_CATEGORY,
        ).order_by("-taxpayer_id").first()
    )
    created = updated = 0
    vivos: list[str] = []
    for period, decl in vigentes_621(taxpayer_id).items():
        pago = resumen_621(decl.casillas)["renta_pago_a_cuenta"]
        if not pago or pago <= 0:
            continue
        date = datetime.date(int(period[:4]), int(period[4:6]), 1)
        fields = {
            "source_object_id": str(decl.pk),
            "direction": TransactionDirection.OUTFLOW,
            "document_kind": "declaracion",
            "description": (
                f"Pago a cuenta de renta · F.V. 621 periodo {period[4:6]}/{period[:4]}"
                f" · orden {decl.nro_orden}"
            ),
            "issue_date": decl.fecha_presentacion or date,
            "accounting_date": date,
            "counterparty_tax_id": "20131312955",
            "counterparty_name": "SUNAT",
            "category": category,
            "categorization_status": CategorizationStatus.CONFIRMED,
            "categorized_at": timezone.now(),
            "settlement_status": "settled",
            **_amounts(Decimal(pago), Decimal("0"), "PEN", Decimal("1")),
        }
        # Una fila por periodo: el id externo es el periodo, no la orden, para
        # que la rectificatoria reemplace y no duplique.
        external_id = f"621-{period}"
        vivos.append(external_id)
        if _upsert(taxpayer_id, TransactionSource.SUNAT_DECLARATION, external_id, fields):
            created += 1
        else:
            updated += 1
    removed, _ = FinancialTransaction.objects.filter(
        taxpayer_id=taxpayer_id, source=TransactionSource.SUNAT_DECLARATION, external_id__startswith="621-",
    ).exclude(external_id__in=vivos).delete()
    return {"created": created, "updated": updated, "removed": removed}


def _category(taxpayer_id: str, code: str):
    from ..models import TransactionCategory

    return (
        TransactionCategory.objects.filter(taxpayer_id__in=["", taxpayer_id], code=code)
        .order_by("-taxpayer_id").first()
    )


def _sunat_fields(description: str, date: datetime.date, amount: Decimal, category, *, source_object_id: str = "", issue_date=None) -> dict:
    from ..models import CategorizationStatus

    return {
        "source_object_id": source_object_id,
        "direction": TransactionDirection.OUTFLOW,
        "document_kind": "declaracion",
        "description": description,
        "issue_date": issue_date or date,
        "accounting_date": date,
        "counterparty_tax_id": "20131312955",
        "counterparty_name": "SUNAT",
        "category": category,
        "categorization_status": CategorizationStatus.CONFIRMED,
        "categorized_at": timezone.now(),
        "settlement_status": "settled",
        **_amounts(amount, Decimal("0"), "PEN", Decimal("1")),
    }


def _sync_source(taxpayer_id: str, source: str, rows: dict[str, dict]) -> dict:
    """Deja la fuente igual a ``rows`` (external_id → fields): alta, cambio y retiro."""
    created = updated = 0
    for external_id, fields in rows.items():
        if _upsert(taxpayer_id, source, external_id, fields):
            created += 1
        else:
            updated += 1
    removed, _ = FinancialTransaction.objects.filter(
        taxpayer_id=taxpayer_id, source=source,
    ).exclude(external_id__in=list(rows)).delete()
    return {"created": created, "updated": updated, "removed": removed}


# ---------------------------------------------------------------- PLAME
def ingest_plame(taxpayer_id: str) -> dict:
    """El costo de personal declarado en la PLAME, para los meses en que el
    módulo de planilla no tiene el periodo cerrado.

    Casillas del 0601: 452 = remuneraciones afectas a EsSalud (la base sobre
    la que SUNAT calcula el 9 %), 412 = EsSalud a cargo del empleador. El
    costo empresa es la suma de ambas; la ONP y la renta de 5.ª son
    retenciones al trabajador y ya van dentro de la remuneración bruta. Los
    honorarios de 4.ª (casilla 320) se excluyen: entran por recibos.

    Si la planilla propia cierra el mes, manda ella: la PLAME se retira.
    """
    from payroll.models import PayrollPeriod, PayrollStatus
    from sunat_declaraciones.models import DeclaracionPresentada, Formulario
    from sunat_declaraciones.services.casillas import numero

    category = _category(taxpayer_id, "PAYROLL_ADMIN")
    cerrados = {
        f"{p.year}{p.month:02d}"
        for p in PayrollPeriod.objects.filter(taxpayer_id=taxpayer_id, status=PayrollStatus.CLOSED)
    }
    vigente: dict[str, DeclaracionPresentada] = {}
    for d in (
        DeclaracionPresentada.objects.de(taxpayer_id).formulario(Formulario.PLAME)
        .exclude(periodo__endswith="13").order_by("periodo", "fecha_presentacion", "nro_orden")
    ):
        vigente[d.periodo] = d
    rows: dict[str, dict] = {}
    for period, decl in vigente.items():
        if period in cerrados:
            continue
        remuneraciones = numero(decl.casillas, "C452") or Decimal("0")
        essalud = numero(decl.casillas, "C412") or Decimal("0")
        costo = remuneraciones + essalud
        if costo <= 0:
            continue
        trabajadores = (decl.constancia or {}).get("trabajadores")
        date = datetime.date(int(period[:4]), int(period[4:6]), 1)
        rows[f"0601-{period}"] = _sunat_fields(
            f"Planilla declarada (PLAME) {period[4:6]}/{period[:4]}"
            + (f" · {trabajadores} trabajador(es)" if trabajadores else "")
            + f" · remuneraciones {remuneraciones:,.0f} + EsSalud {essalud:,.0f} · orden {decl.nro_orden}",
            date, costo, category, source_object_id=str(decl.pk), issue_date=decl.fecha_presentacion or date,
        )
    return _sync_source(taxpayer_id, TransactionSource.SUNAT_PLAME, rows)


# ------------------------------------------------------------ DJ anual
def ingest_annual(taxpayer_id: str) -> dict:
    """Del 710 salen dos cosas que los comprobantes no ven.

    * **Depreciación** del ejercicio = variación de la depreciación acumulada
      (casilla 383) frente al 710 anterior; sin anterior, la acumulada entera.
      Se reparte en doceavos, a la línea informativa que alimenta el EBITDA.
    * **Ajuste del impuesto**: la línea mensual lleva pagos a cuenta; en
      diciembre se ajusta para que el año sume el impuesto determinado
      (casilla 113). Con pérdida, el ajuste devuelve los pagos a cuenta.
    """
    from sunat_declaraciones.services.casillas import numero
    from sunat_declaraciones.services.renta_anual import vigentes

    cat_dep = _category(taxpayer_id, "DEPRECIATION")
    cat_tax = _category(taxpayer_id, "INCOME_TAX")
    anuales = vigentes(taxpayer_id)
    rows: dict[str, dict] = {}
    for ejercicio, decl in anuales.items():
        year = int(ejercicio)
        c = decl.casillas
        acumulada = numero(c, "383")
        anterior = anuales.get(str(year - 1))
        previa = numero(anterior.casillas, "383") if anterior else None
        if acumulada is not None:
            del_anio = acumulada - (previa or Decimal("0"))
            if del_anio > 0:
                mensual = (del_anio / 12).quantize(Decimal("0.01"))
                for m in range(1, 13):
                    rows[f"710-{ejercicio}-dep-{m:02d}"] = _sunat_fields(
                        f"Depreciación {ejercicio} según DJ anual (casilla 383) · {del_anio:,.0f} en doceavos · orden {decl.nro_orden}",
                        datetime.date(year, m, 1), mensual, cat_dep, source_object_id=str(decl.pk),
                    )
        impuesto = numero(c, "113")
        if impuesto is not None:
            pagos = FinancialTransaction.objects.filter(
                taxpayer_id=taxpayer_id, source=TransactionSource.SUNAT_DECLARATION,
                accounting_date__year=year, external_id__startswith="621-",
            ).aggregate(t=Sum("net_amount_pen"))["t"] or Decimal("0")
            ajuste = impuesto - pagos  # positivo = falta impuesto; negativo = pagos de más
            if ajuste != 0:
                rows[f"710-{ejercicio}-tax"] = _sunat_fields(
                    f"Ajuste del impuesto a la renta {ejercicio} según DJ anual: impuesto {impuesto:,.0f} − pagos a cuenta {pagos:,.0f} · orden {decl.nro_orden}",
                    datetime.date(year, 12, 1), ajuste, cat_tax, source_object_id=str(decl.pk),
                )
    return _sync_source(taxpayer_id, TransactionSource.SUNAT_ANNUAL, rows)


# ------------------------------------------------------------- multas
def ingest_penalties(taxpayer_id: str) -> dict:
    """Multas e intereses pagados con boletas 1662 (tributos 6xxx en la
    constancia). El fraccionamiento no entra: es deuda vieja, no gasto."""
    from sunat_declaraciones.models import DeclaracionPresentada, Formulario
    from sunat_declaraciones.services.tributos import tributos_de

    category = _category(taxpayer_id, "SUNAT_PENALTIES")
    rows: dict[str, dict] = {}
    for b in DeclaracionPresentada.objects.de(taxpayer_id).formulario(Formulario.BOLETA):
        multas = [t for t in tributos_de(b.constancia) if t["clase"] == "multa" and t["importe"]]
        total = sum((t["importe"] for t in multas), Decimal("0"))
        if total <= 0:
            continue
        fecha = (b.fecha_pago.date() if b.fecha_pago else b.fecha_presentacion) or datetime.date(int(b.periodo[:4]), int(b.periodo[4:6]), 1)
        rows[f"1662-{b.nro_orden}"] = _sunat_fields(
            f"Multa SUNAT · {' / '.join(t['descripcion'] for t in multas)} · periodo {b.periodo[4:6]}/{b.periodo[:4]} · orden {b.nro_orden}",
            fecha, total, category, source_object_id=str(b.pk),
        )
    return _sync_penalties(taxpayer_id, rows)


def _sync_penalties(taxpayer_id: str, rows: dict[str, dict]) -> dict:
    """Las multas comparten fuente con los pagos a cuenta (``sunat_declaration``);
    el retiro se limita a los ids ``1662-…`` para no tocar los ``621-…``."""
    created = updated = 0
    for external_id, fields in rows.items():
        if _upsert(taxpayer_id, TransactionSource.SUNAT_DECLARATION, external_id, fields):
            created += 1
        else:
            updated += 1
    removed, _ = FinancialTransaction.objects.filter(
        taxpayer_id=taxpayer_id, source=TransactionSource.SUNAT_DECLARATION, external_id__startswith="1662-",
    ).exclude(external_id__in=list(rows)).delete()
    return {"created": created, "updated": updated, "removed": removed}


def ingest_sunat_declarations(taxpayer_id: str) -> dict:
    """Todo lo que viene de SUNAT y no de comprobantes, en el orden que
    importa: el ajuste anual necesita los pagos a cuenta ya cargados."""
    return {
        "declarations": ingest_declarations(taxpayer_id),
        "plame": ingest_plame(taxpayer_id),
        "penalties": ingest_penalties(taxpayer_id),
        "annual": ingest_annual(taxpayer_id),
    }


def ingest_all(taxpayer_id: str) -> dict:
    return {
        "sunat": ingest_sunat(taxpayer_id),
        "manual": ingest_manual(taxpayer_id),
        "payroll": ingest_payroll(taxpayer_id),
        "fee_receipts": ingest_fee_receipts(taxpayer_id),
        "sunat_declaraciones": ingest_sunat_declarations(taxpayer_id),
    }
