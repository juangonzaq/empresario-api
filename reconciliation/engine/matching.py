"""Invoice ↔ bank-movement matching engine.

Deterministic and explainable: every link carries its evidence. Relations are
not assumed 1:1 — one invoice may be paid in several movements, one movement
may settle several invoices, payments may be partial, early or late. What the
rules cannot decide stays ``undetermined``; nothing is forced.
"""

from __future__ import annotations

import datetime
import os
import re
from decimal import Decimal
from itertools import combinations
from typing import Any

from django.db import transaction

from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

from ..models import (
    BankMovement, InvoiceSettlement, MovementCategory, MovementKind, SettlementLine,
    SettlementStatus,
)
from .normalization import from_cpe

AMOUNT_TOLERANCE = Decimal(os.getenv("RECON_MATCH_AMOUNT_TOLERANCE", "1.00"))
# A payment may land well after (or slightly before) the invoice.
DAYS_BEFORE = int(os.getenv("RECON_MATCH_DAYS_BEFORE", "15"))
DAYS_AFTER = int(os.getenv("RECON_MATCH_DAYS_AFTER", "180"))
COMBO_LIMIT = 3  # subset size cap: keeps the search bounded and explainable


def _tokens(name: str) -> set[str]:
    stop = {"SAC", "S.A.C.", "SA", "S.A.", "EIRL", "E.I.R.L.", "SRL", "S.R.L.", "DE", "DEL", "LA", "EL", "Y", "PERU"}
    return {t for t in re.split(r"[^A-ZÑ0-9]+", (name or "").upper()) if len(t) >= 3 and t not in stop}


def _name_in_description(name: str, description: str) -> bool:
    toks = _tokens(name)
    if not toks:
        return False
    desc = (description or "").upper()
    hits = sum(1 for t in toks if t in desc)
    return hits >= max(1, min(2, len(toks) - 1))


def _date_score(invoice_date: datetime.date | None, movement_date: datetime.date) -> float | None:
    if invoice_date is None:
        return 0.0
    delta = (movement_date - invoice_date).days
    if delta < -DAYS_BEFORE or delta > DAYS_AFTER:
        return None
    if -2 <= delta <= 7:
        return 0.2
    if delta <= 30:
        return 0.12
    return 0.05


def _evidence(amount_exact: bool, delta_days: int | None, name_hit: bool, ruc_hit: bool, number_hit: bool) -> list[str]:
    ev = []
    ev.append("Monto exacto" if amount_exact else "Suma de montos coincide")
    if delta_days is not None:
        ev.append(f"Fecha a {abs(delta_days)} día(s) del comprobante")
    if ruc_hit:
        ev.append("RUC presente en la descripción bancaria")
    if name_hit:
        ev.append("Razón social presente en la descripción bancaria")
    if number_hit:
        ev.append("Número de comprobante u operación presente en la descripción")
    return ev


def _open_invoices(account_ruc: str) -> list[ElectronicInvoice]:
    return list(
        ElectronicInvoice.objects.for_account(account_ruc)
        .filter(direction=Direction.ISSUED, document_class=DocumentClass.INVOICE,
                is_cancelled=False, is_rejected=False)
        .select_related("extract", "override").defer("xml_content", "raw")
    )


def _norm_number(value: str) -> str:
    # «E001 - 487» y «E001-487» son el mismo comprobante; los espacios varían
    # según de dónde vino el dato (portal, XML, referencia de la NC).
    return re.sub(r"\s", "", (value or "").upper())


def _credit_notes_by_invoice(account_ruc: str) -> dict[str, Decimal]:
    """Total de notas de crédito emitidas por factura referenciada.

    Una NC reduce lo cobrable: el cliente paga factura − NC, y una factura
    anulada por completo no es una cuenta por cobrar. Las NC sin referencia
    legible no se asignan a nadie — mejor un saldo de más que un descuento
    inventado sobre la factura equivocada.
    """
    notes = (
        ElectronicInvoice.objects.for_account(account_ruc)
        .filter(direction=Direction.ISSUED, document_class=DocumentClass.CREDIT_NOTE,
                is_cancelled=False, is_rejected=False)
        .exclude(references_document="")
        .select_related("extract", "override").defer("xml_content", "raw")
    )
    by_invoice: dict[str, Decimal] = {}
    for note in notes:
        key = _norm_number(note.references_document)
        amount = from_cpe(note).total or Decimal("0")
        by_invoice[key] = by_invoice.get(key, Decimal("0")) + amount
    return by_invoice


def _match_score(inv, mov: BankMovement) -> tuple[float, list[str]] | None:
    doc = from_cpe(inv)
    if (doc.currency or "PEN") != (mov.currency or "PEN"):
        return None
    total = doc.total or Decimal("0")
    if total <= 0:
        return None
    date_score = _date_score(doc.issue_date, mov.date)
    if date_score is None:
        return None
    desc = (mov.description or "").upper()
    ruc_hit = bool(doc.counterparty_ruc) and doc.counterparty_ruc in desc
    name_hit = _name_in_description(doc.counterparty_name, desc)
    number_hit = bool(doc.series) and f"{doc.series}-{doc.number}".upper() in desc
    exact = abs(total - mov.amount) <= AMOUNT_TOLERANCE
    score = (0.6 if exact else 0.0) + date_score + (0.2 if (ruc_hit or name_hit or number_hit) else 0.0)
    if not exact and not (ruc_hit or name_hit or number_hit):
        return None  # nothing ties them together
    delta = (mov.date - doc.issue_date).days if doc.issue_date else None
    return score, _evidence(exact, delta, name_hit, ruc_hit, number_hit)


@transaction.atomic
def rebuild_settlements(account_ruc: str) -> dict[str, Any]:
    """Recompute every invoice's collection state from unassigned credits.

    Manual links (``matched_by=user``) are preserved; everything decided by
    rules is recomputed from scratch, which keeps the engine idempotent.
    """
    invoices = _open_invoices(account_ruc)
    credits = list(BankMovement.objects.filter(account_ruc=account_ruc, kind=MovementKind.CREDIT))
    notes = _credit_notes_by_invoice(account_ruc)

    SettlementLine.objects.filter(settlement__account_ruc=account_ruc, matched_by="rules").delete()

    settlements: dict[str, InvoiceSettlement] = {}
    for inv in invoices:
        doc = from_cpe(inv)
        st, _ = InvoiceSettlement.objects.get_or_create(
            account_ruc=account_ruc, invoice=inv,
            defaults={"invoice_total": doc.total or Decimal("0"), "billing_period": inv.period},
        )
        st.invoice_total = doc.total or Decimal("0")
        # Tope en el total: una NC mayor que su factura es un dato raro, no un
        # crédito a favor que haya que perseguir en el banco.
        st.credit_notes_amount = min(notes.get(_norm_number(inv.full_number), Decimal("0")), st.invoice_total)
        settlements[str(inv.pk)] = st

    # Capacity left on each movement after manual links.
    used: dict[str, Decimal] = {}
    for line in SettlementLine.objects.filter(settlement__account_ruc=account_ruc).select_related("movement"):
        used[str(line.movement_id)] = used.get(str(line.movement_id), Decimal("0")) + line.amount

    def capacity(m: BankMovement) -> Decimal:
        return m.amount - used.get(str(m.pk), Decimal("0"))

    def paid(st: InvoiceSettlement) -> Decimal:
        return sum((l.amount for l in st.lines.all()), Decimal("0"))

    def remaining(st: InvoiceSettlement) -> Decimal:
        # Lo que de verdad falta cobrar: el cliente paga factura − NC.
        return st.invoice_total - st.credit_notes_amount - paid(st)

    def link(st: InvoiceSettlement, mov: BankMovement, amount: Decimal, score: float, ev: list[str]) -> None:
        SettlementLine.objects.create(settlement=st, movement=mov, amount=amount, confidence=round(score, 2), evidence=ev)
        used[str(mov.pk)] = used.get(str(mov.pk), Decimal("0")) + amount

    # Pass 1 — exact one-to-one, best score first.
    candidates = []
    for inv in invoices:
        st = settlements[str(inv.pk)]
        pending = remaining(st)
        if pending <= AMOUNT_TOLERANCE:
            continue
        for mov in credits:
            if abs(capacity(mov) - pending) > AMOUNT_TOLERANCE:
                continue
            scored = _match_score(inv, mov)
            if scored:
                candidates.append((scored[0], inv, mov, pending, scored[1]))
    for score, inv, mov, pending, ev in sorted(candidates, key=lambda c: -c[0]):
        st = settlements[str(inv.pk)]
        if remaining(st) <= AMOUNT_TOLERANCE or capacity(mov) < pending - AMOUNT_TOLERANCE:
            continue
        link(st, mov, pending, score, ev)

    # Pass 2 — one movement settles several invoices of the same counterparty.
    for mov in credits:
        cap = capacity(mov)
        if cap <= AMOUNT_TOLERANCE:
            continue
        open_by_party: dict[str, list] = {}
        for inv in invoices:
            st = settlements[str(inv.pk)]
            pending = remaining(st)
            if pending > AMOUNT_TOLERANCE and _match_score(inv, mov):
                open_by_party.setdefault(from_cpe(inv).counterparty_ruc or "-", []).append((inv, pending))
        for party, items in open_by_party.items():
            found = False
            for size in range(2, min(COMBO_LIMIT, len(items)) + 1):
                for combo in combinations(items, size):
                    if abs(sum(r for _, r in combo) - cap) <= AMOUNT_TOLERANCE:
                        for inv, pending in combo:
                            scored = _match_score(inv, mov)
                            link(settlements[str(inv.pk)], mov, pending, scored[0] * 0.9, scored[1])
                        found = True
                        break
                if found:
                    break

    # Pass 3 — partial payments strongly tied by counterparty in description.
    for inv in invoices:
        st = settlements[str(inv.pk)]
        pending = remaining(st)
        if pending <= AMOUNT_TOLERANCE:
            continue
        for mov in sorted(credits, key=lambda m: m.date):
            cap = capacity(mov)
            if cap <= AMOUNT_TOLERANCE:
                continue
            scored = _match_score(inv, mov)
            if not scored:
                continue
            desc_tied = any("descripción" in e for e in scored[1])
            if not desc_tied:
                continue
            amount = min(cap, pending)
            link(st, mov, amount, min(scored[0], 0.7), scored[1] + ["Pago parcial"])
            pending -= amount
            if pending <= AMOUNT_TOLERANCE:
                break

    stats = {"paid": 0, "partial": 0, "unpaid": 0, "overpaid": 0, "credited": 0}
    for st in settlements.values():
        total_paid = paid(st)
        st.paid_amount = total_paid
        st.balance = st.invoice_total - st.credit_notes_amount - total_paid
        last = st.lines.select_related("movement").order_by("-movement__date").first()
        st.last_payment_date = last.movement.date if last else None
        st.collection_period = last.movement.period if last else ""
        if st.balance > AMOUNT_TOLERANCE:
            # Con NC de por medio el saldo ya es el neto; «sin pago» sigue
            # significando que del banco no llegó nada.
            st.status = SettlementStatus.PARTIAL if total_paid > 0 else SettlementStatus.UNPAID
        elif st.balance >= -AMOUNT_TOLERANCE:
            if total_paid <= 0 and st.credit_notes_amount > 0:
                st.status = SettlementStatus.CREDITED
            else:
                st.status = SettlementStatus.PAID
        else:
            st.status = SettlementStatus.OVERPAID
        st.save()
        stats[st.status] += 1

    # Movements that pay invoices get their category confirmed.
    for mov in credits:
        if used.get(str(mov.pk)) and mov.classified_by != BankMovement.ClassifiedBy.USER:
            mov.category = MovementCategory.INVOICE_COLLECTION
            mov.confidence = 0.9
            mov.evidence = ["Asignado a comprobante(s) por el motor de matching"]
            mov.classified_by = BankMovement.ClassifiedBy.RULES
            mov.save(update_fields=["category", "confidence", "evidence", "classified_by", "updated_at"])
    return stats
