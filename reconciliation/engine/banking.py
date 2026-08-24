"""Rule-based classification of bank movements.

Order matters and is deliberate: settlement matching already claimed the
collections; here the rest is classified by explainable rules. AI is allowed
only as an *auxiliary* for what the rules leave ``unidentified`` — and its
verdicts are stored with ``classified_by=ai`` so they are never confused with
deterministic ones. A user decision always wins and is never recomputed.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..models import BankMovement, MovementCategory, MovementKind

RULES: list[tuple[str, str, str]] = [
    # (category, regex over description, evidence label)
    (MovementCategory.TAX_PAYMENT, r"SUNAT|NPS|PAGO\s*TRIBUT|DETRACC|IMPUESTO", "Descripción menciona tributos/SUNAT"),
    (MovementCategory.PAYROLL_PAYMENT, r"PLANILLA|HABERES|REMUNERAC|CTS|GRATIFICAC|\bAFP\b|ESSALUD|ONP", "Descripción menciona planilla/aportes"),
    (MovementCategory.OWN_ACCOUNT_TRANSFER, r"MISMO\s*TITULAR|CTAS?\.?\s*PROPIAS?|TRANSF.*PROPIA|TRASLADO\s*ENTRE\s*CUENTAS", "Descripción indica cuentas propias"),
    (MovementCategory.LOAN, r"PR[ÉE]STAMO|DESEMBOLSO|CR[ÉE]DITO\s+(BANCARIO|PYME)|LEASING", "Descripción menciona préstamo/desembolso"),
    (MovementCategory.CAPITAL_CONTRIBUTION, r"APORTE\s*(DE)?\s*CAPITAL|CAPITALIZAC", "Descripción menciona aporte de capital"),
    (MovementCategory.REFUND, r"DEVOLUC|EXTORNO(?!.*ERROR)", "Descripción menciona devolución"),
    (MovementCategory.REIMBURSEMENT, r"REEMBOLSO", "Descripción menciona reembolso"),
]


def _own_transfer_pair(mov: BankMovement, all_movements: list[BankMovement]) -> BankMovement | None:
    """Same amount, opposite direction, ±2 days, different account: likely a
    transfer between the company's own accounts."""
    for other in all_movements:
        if other.pk == mov.pk or other.kind == mov.kind:
            continue
        if other.bank_account and mov.bank_account and other.bank_account == mov.bank_account:
            continue
        if abs(other.amount - mov.amount) <= Decimal("0.01") and abs((other.date - mov.date).days) <= 2:
            return other
    return None


def _counterparty_hit(mov: BankMovement, supplier_names: dict[str, str]) -> tuple[str, str] | None:
    desc = (mov.description or "").upper()
    for ruc, name in supplier_names.items():
        if ruc and ruc in desc:
            return ruc, f"RUC {ruc} presente en la descripción"
        tokens = [t for t in re.split(r"[^A-ZÑ0-9]+", name.upper()) if len(t) >= 4][:2]
        if tokens and all(t in desc for t in tokens):
            return ruc, f"Razón social «{name[:40]}» presente en la descripción"
    return None


def _known_parties(account_ruc: str, direction: str) -> dict[str, str]:
    from sunat_cpe.models import Direction, ElectronicInvoice

    qs = ElectronicInvoice.objects.for_account(account_ruc).filter(
        direction=Direction.RECEIVED if direction == "purchases" else Direction.ISSUED
    ).values_list("issuer_ruc" if direction == "purchases" else "receiver_ruc",
                  "issuer_name" if direction == "purchases" else "receiver_name")
    return {(r or "").strip(): (n or "").strip() for r, n in qs if r and n}


def classify_movements(account_ruc: str) -> dict[str, int]:
    """Classify every movement not already decided by a user (or by matching)."""
    movements = list(BankMovement.objects.filter(account_ruc=account_ruc))
    suppliers = _known_parties(account_ruc, "purchases")
    customers = _known_parties(account_ruc, "sales")
    counts: dict[str, int] = {}

    for mov in movements:
        if mov.classified_by == BankMovement.ClassifiedBy.USER:
            continue
        if mov.category == MovementCategory.INVOICE_COLLECTION and mov.classified_by == BankMovement.ClassifiedBy.RULES:
            continue  # settled by the matcher
        category, confidence, evidence = MovementCategory.UNIDENTIFIED, None, []

        pair = _own_transfer_pair(mov, movements)
        if pair is not None:
            category, confidence = MovementCategory.OWN_ACCOUNT_TRANSFER, 0.85
            evidence = [f"Movimiento espejo el {pair.date} por el mismo importe en otra cuenta"]
        else:
            for cat, pattern, label in RULES:
                if re.search(pattern, (mov.description or "").upper()):
                    category, confidence, evidence = cat, 0.8, [label]
                    break
        if category == MovementCategory.UNIDENTIFIED and mov.kind == MovementKind.DEBIT:
            hit = _counterparty_hit(mov, suppliers)
            if hit:
                category, confidence, evidence = MovementCategory.SUPPLIER_PAYMENT, 0.7, [hit[1]]
        if category == MovementCategory.UNIDENTIFIED and mov.kind == MovementKind.CREDIT:
            hit = _counterparty_hit(mov, customers)
            if hit:
                category, confidence = MovementCategory.INVOICE_COLLECTION, 0.6
                evidence = [hit[1], "Sin comprobante asignado todavía"]

        mov.category = category
        mov.confidence = confidence
        mov.evidence = evidence
        mov.classified_by = BankMovement.ClassifiedBy.RULES if category != MovementCategory.UNIDENTIFIED else ""
        mov.save(update_fields=["category", "confidence", "evidence", "classified_by", "updated_at"])
        counts[category] = counts.get(category, 0) + 1
    return counts


def pending_amount(account_ruc: str, period: str) -> Decimal:
    """Credits of the period still pending classification — the number the
    dashboard shows as «movimientos por clasificar», never as sales."""
    return sum((
        m.amount for m in BankMovement.objects.filter(
            account_ruc=account_ruc, period=period, kind=MovementKind.CREDIT,
            category=MovementCategory.UNIDENTIFIED,
        )
    ), Decimal("0"))
