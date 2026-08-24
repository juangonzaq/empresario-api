"""CPE ↔ SIRE (RVIE/RCE) document-level reconciliation.

For each direction the engine indexes both sides by the normalized document
key and classifies every key it sees. Differences carry a level per spec
(OK / warning / review / critical) — and a note with the *possible* benign
explanation whenever there is a common one (consolidated boletas, timing).
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from ..models import DocMatchStatus, MatchLevel
from .normalization import BOLETA, NormalizedDoc, load_cpe, load_sire, normalize_doc_type

AMOUNT_TOLERANCE = Decimal(os.getenv("RECON_AMOUNT_TOLERANCE", "1.00"))
DATE_TOLERANCE_DAYS = int(os.getenv("RECON_DATE_TOLERANCE_DAYS", "3"))
CRITICAL_AMOUNT = Decimal(os.getenv("RECON_CRITICAL_AMOUNT", "1000"))


def _money(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def _level_for_missing(doc: NormalizedDoc) -> tuple[str, list[str]]:
    notes: list[str] = []
    if normalize_doc_type(doc.doc_type) == BOLETA:
        notes.append("Las boletas suelen registrarse consolidadas en el resumen diario; revisa antes de corregir.")
        return MatchLevel.WARNING, notes
    if doc.cancelled:
        notes.append("El comprobante figura anulado; que no esté en el registro puede ser correcto.")
        return MatchLevel.WARNING, notes
    amount = doc.total or Decimal("0")
    return (MatchLevel.CRITICAL if abs(amount) >= CRITICAL_AMOUNT else MatchLevel.REVIEW), notes


def _compare(cpe: NormalizedDoc, sire: NormalizedDoc) -> dict[str, Any]:
    diffs: dict[str, Any] = {"notes": []}
    status, level = DocMatchStatus.MATCHED, MatchLevel.OK

    if cpe.cancelled and not sire.is_credit_note:
        status, level = DocMatchStatus.CANCELLED_MISMATCH, MatchLevel.REVIEW
        diffs["notes"].append("El CPE está anulado pero aparece registrado; verifica la baja en el registro.")

    if cpe.total is not None and sire.total is not None:
        diff = cpe.total - sire.total
        if abs(diff) > AMOUNT_TOLERANCE:
            diffs["amount_diff"] = _money(diff)
            status = DocMatchStatus.AMOUNT_MISMATCH
            level = MatchLevel.CRITICAL if abs(diff) >= CRITICAL_AMOUNT else MatchLevel.WARNING
    if cpe.igv is not None and sire.igv is not None:
        diff = cpe.igv - sire.igv
        if abs(diff) > AMOUNT_TOLERANCE:
            diffs["igv_diff"] = _money(diff)
            if status == DocMatchStatus.MATCHED:
                status = DocMatchStatus.IGV_MISMATCH
            level = MatchLevel.CRITICAL if abs(diff) >= CRITICAL_AMOUNT else max_level(level, MatchLevel.WARNING)
    if cpe.issue_date and sire.issue_date:
        delta = abs((cpe.issue_date - sire.issue_date).days)
        if delta > DATE_TOLERANCE_DAYS:
            diffs["date_diff_days"] = delta
            if status == DocMatchStatus.MATCHED:
                status = DocMatchStatus.DATE_MISMATCH
            level = max_level(level, MatchLevel.WARNING)
    if cpe.counterparty_ruc and sire.counterparty_ruc and cpe.counterparty_ruc != sire.counterparty_ruc:
        diffs["counterparty"] = {"cpe": cpe.counterparty_ruc, "sire": sire.counterparty_ruc}
        if status == DocMatchStatus.MATCHED:
            status = DocMatchStatus.PARTY_MISMATCH
        level = max_level(level, MatchLevel.REVIEW)
    return {"status": status, "level": level, "differences": diffs}


_ORDER = [MatchLevel.OK, MatchLevel.WARNING, MatchLevel.REVIEW, MatchLevel.CRITICAL]


def max_level(a: str, b: str) -> str:
    return a if _ORDER.index(a) >= _ORDER.index(b) else b


def reconcile_direction(account_ruc: str, period: str, direction: str) -> dict[str, Any]:
    """Returns rows (dicts ready for DocumentReconciliation) plus totals."""
    cpe_docs = [d for d in load_cpe(account_ruc, period, direction)]
    sire_docs = load_sire(account_ruc, period, direction)
    cpe_by_key = {d.key: d for d in cpe_docs}
    sire_by_key = {d.key: d for d in sire_docs}

    rows: list[dict[str, Any]] = []
    for key in sorted(set(cpe_by_key) | set(sire_by_key)):
        cpe, sire = cpe_by_key.get(key), sire_by_key.get(key)
        base = {
            "direction": direction, "doc_key": key,
            "counterparty_ruc": (cpe or sire).counterparty_ruc,
            "counterparty_name": (cpe or sire).counterparty_name,
            "cpe_id": cpe.source_id if cpe else None,
            "sire_id": sire.source_id if sire else None,
            "cpe_total": cpe.total if cpe else None, "sire_total": sire.total if sire else None,
            "cpe_igv": cpe.igv if cpe else None, "sire_igv": sire.igv if sire else None,
        }
        if cpe and sire:
            rows.append({**base, **_compare(cpe, sire)})
        elif cpe and not cpe.cancelled:
            level, notes = _level_for_missing(cpe)
            rows.append({**base, "status": DocMatchStatus.CPE_ONLY, "level": level, "differences": {"notes": notes}})
        elif cpe:  # cancelled and absent: fine
            rows.append({**base, "status": DocMatchStatus.MATCHED, "level": MatchLevel.OK,
                         "differences": {"notes": ["Anulado y fuera del registro: consistente."]}})
        else:
            level, notes = _level_for_missing(sire)
            rows.append({**base, "status": DocMatchStatus.SIRE_ONLY, "level": level, "differences": {"notes": notes}})

    active = [d for d in cpe_docs if not d.cancelled]
    sign = lambda d: -1 if d.is_credit_note else 1  # noqa: E731
    totals = {
        "cpe_total": sum((sign(d) * (d.total or Decimal("0")) for d in active), Decimal("0")),
        "cpe_igv": sum((sign(d) * (d.igv or Decimal("0")) for d in active), Decimal("0")),
        "sire_total": sum((sign(d) * (d.total or Decimal("0")) for d in sire_docs), Decimal("0")),
        "sire_base": sum((sign(d) * (d.base_amount or Decimal("0")) for d in sire_docs), Decimal("0")),
        "sire_igv": sum((sign(d) * (d.igv or Decimal("0")) for d in sire_docs), Decimal("0")),
        "sire_rows": len(sire_docs), "cpe_rows": len(cpe_docs),
        "sire_available": bool(sire_docs),
    }
    return {"rows": rows, "totals": totals}
