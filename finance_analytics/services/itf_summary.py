"""Executive ITF aggregates.

The ITF report is ALWAYS presented as «movimientos bancarios reportados para
efectos del ITF» — never as balance, income, collections, profit or cash flow.

Movements are split by direction using the SUNAT ``operation_code``:
codes 12 and 13 are **acreditaciones reportadas** (money in), codes 14 and 15
are **débitos reportados** (money out). Any other code stays unclassified and
is never guessed into a direction; the catalog can be extended via the
``FINANCE_ITF_OPERATION_CATALOG`` env var without touching code.

The sum of both directions is the *movimiento bruto*: a secondary figure only,
because adding inflows and outflows produces a number that is neither
facturación, ingresos, flujo de caja nor saldo bancario.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

from django.conf import settings

from sunat_itf.models import ItfRecord

from .common import THRESHOLDS, money, pct_change, period_label

MEANING = (
    "Movimientos bancarios reportados para efectos del ITF. No representa "
    "saldo, ingresos, cobranza, utilidad ni flujo de caja."
)

GROSS_MOVEMENT_NOTE = (
    "El movimiento bruto suma los dos sentidos (acreditaciones + débitos), por "
    "lo que un mismo sol que entra y luego sale se cuenta dos veces. No "
    "representa facturación, ingresos, flujo de caja ni saldo bancario; sirve "
    "solo como referencia del volumen operado."
)

# Verified direction of SUNAT's operation codes. 12/13 = acreditaciones
# (entradas), 14/15 = débitos (salidas). Any code outside this catalog is
# reported as «sin clasificar»: an unverified guess would misstate the
# direction of real money movements.
DEFAULT_OPERATION_CATALOG: dict[str, dict[str, str]] = {
    "12": {"label": "Acreditación reportada · código 12", "flow": "in"},
    "13": {"label": "Acreditación reportada · código 13", "flow": "in"},
    "14": {"label": "Débito reportado · código 14", "flow": "out"},
    "15": {"label": "Débito reportado · código 15", "flow": "out"},
}

FLOW_LABEL = {"in": "Acreditaciones reportadas", "out": "Débitos reportados"}

# Human-readable name of what each month-over-month comparison measures, so a
# variation is never shown without saying what it compares.
VARIATION_BASIS_LABEL = {
    "inflow": "entradas (acreditaciones reportadas)",
    "outflow": "salidas (débitos reportados)",
    "gross": "movimiento bruto (entradas + salidas)",
}


def _load_catalog() -> dict[str, dict[str, str]]:
    """Default catalog, overridable per code via env (JSON object)."""
    catalog = {code: dict(entry) for code, entry in DEFAULT_OPERATION_CATALOG.items()}
    raw = os.getenv("FINANCE_ITF_OPERATION_CATALOG", "")
    if not raw:
        return catalog
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return catalog
    if isinstance(parsed, dict):
        for code, entry in parsed.items():
            if isinstance(entry, dict):
                catalog[normalize_code(str(code))] = entry
    return catalog


def normalize_code(code: str) -> str:
    """'  012 ' → '12'. SUNAT renders the code with padding in some reports."""
    cleaned = (code or "").strip()
    stripped = cleaned.lstrip("0")
    return stripped or cleaned


OPERATION_CATALOG = _load_catalog()


def describe_code(code: str) -> dict[str, Any]:
    normalized = normalize_code(code)
    entry = OPERATION_CATALOG.get(normalized)
    if entry:
        return {
            "code": normalized,
            "label": entry.get("label", f"Código {normalized}"),
            "flow": entry.get("flow"),
            "flow_label": FLOW_LABEL.get(entry.get("flow", ""), "Sin clasificar"),
            "classified": entry.get("flow") in ("in", "out"),
        }
    return {
        "code": normalized or "—",
        "label": f"Código {normalized or '—'} · sin clasificar",
        "flow": None,
        "flow_label": "Sin clasificar",
        "classified": False,
    }


def _blank_period() -> dict[str, Decimal | int]:
    return {
        "inflow_base": Decimal("0"),
        "inflow_tax": Decimal("0"),
        "inflow_records": 0,
        "outflow_base": Decimal("0"),
        "outflow_tax": Decimal("0"),
        "outflow_records": 0,
        "unclassified_base": Decimal("0"),
        "unclassified_tax": Decimal("0"),
        "unclassified_records": 0,
    }


def _atypical(inflow: float | None, outflow: float | None, gross: float | None
              ) -> tuple[bool, str | None]:
    """Flag the month only when a *directional* move is out of range; the gross
    movement is the fallback and is labelled as such."""
    limit = THRESHOLDS["variation_alert_pct"]
    for basis, value in (("inflow", inflow), ("outflow", outflow)):
        if value is not None and abs(value) >= limit:
            return True, basis
    if gross is not None and abs(gross) >= limit:
        return True, "gross"
    return False, None


def _aggregate(rows: list[ItfRecord]) -> tuple[dict, dict, dict]:
    """One month's records → (directional totals, per-code, per-bank)."""
    totals = _blank_period()
    codes: dict[str, dict[str, Any]] = {}
    banks: dict[str, dict[str, Decimal]] = {}

    for r in rows:
        described = describe_code(r.operation_code or "")
        base = r.base_amount or Decimal("0")
        tax = r.tax or Decimal("0")
        prefix = {"in": "inflow", "out": "outflow"}.get(described["flow"], "unclassified")
        totals[f"{prefix}_base"] += base
        totals[f"{prefix}_tax"] += tax
        totals[f"{prefix}_records"] += 1

        code = codes.setdefault(described["code"], {
            **described, "base": Decimal("0"), "tax": Decimal("0"), "records": 0,
        })
        code["base"] += base
        code["tax"] += tax
        code["records"] += 1

        name = r.declarant_name or r.declarant_ruc or "—"
        bank = banks.setdefault(name, {
            "base": Decimal("0"), "inflow": Decimal("0"), "outflow": Decimal("0"),
        })
        bank["base"] += base
        if described["flow"] in ("in", "out"):
            bank["inflow" if described["flow"] == "in" else "outflow"] += base

    return totals, codes, banks


def _period_row(
    period: str, rows: list[ItfRecord], prev: dict[str, float] | None
) -> dict[str, Any]:
    totals, codes, banks = _aggregate(rows)

    inflow = money(totals["inflow_base"])
    outflow = money(totals["outflow_base"])
    gross = money(
        totals["inflow_base"] + totals["outflow_base"] + totals["unclassified_base"]
    )

    variation_inflow = pct_change(inflow, prev["inflow"] if prev else None)
    variation_outflow = pct_change(outflow, prev["outflow"] if prev else None)
    variation_gross = pct_change(gross, prev["gross"] if prev else None)
    atypical, basis = _atypical(variation_inflow, variation_outflow, variation_gross)

    return {
        "period": period,
        "label": period_label(period),
        # Entradas / salidas: la lectura principal del mes.
        "inflow_base": inflow,
        "inflow_tax": money(totals["inflow_tax"]),
        "inflow_records": totals["inflow_records"],
        "outflow_base": outflow,
        "outflow_tax": money(totals["outflow_tax"]),
        "outflow_records": totals["outflow_records"],
        "unclassified_base": money(totals["unclassified_base"]),
        "unclassified_records": totals["unclassified_records"],
        # Movimiento bruto: dato secundario, suma de ambos sentidos.
        "gross_movement": gross,
        "gross_movement_note": GROSS_MOVEMENT_NOTE,
        "total_tax": money(
            totals["inflow_tax"] + totals["outflow_tax"] + totals["unclassified_tax"]
        ),
        "records": len(rows),
        "by_code": [
            {**c, "base": money(c["base"]), "tax": money(c["tax"])}
            for c in sorted(codes.values(), key=lambda c: c["code"])
        ],
        "by_bank": [
            {
                "name": name,
                "base": money(a["base"]),
                "inflow": money(a["inflow"]),
                "outflow": money(a["outflow"]),
            }
            for name, a in banks.items()
        ],
        # Cada variación dice explícitamente qué compara.
        "variation_inflow_pct": variation_inflow,
        "variation_outflow_pct": variation_outflow,
        "variation_gross_pct": variation_gross,
        "atypical": atypical,
        "atypical_basis": basis,
        "atypical_basis_label": VARIATION_BASIS_LABEL[basis] if basis else None,
    }


def itf_summary(taxpayer_id: str | None = None, months: int = 12) -> dict[str, Any]:
    ruc = taxpayer_id or settings.SUNAT_RUC
    records = list(ItfRecord.objects.for_taxpayer(ruc))
    if not records:
        return {
            "meaning": MEANING,
            "gross_movement_note": GROSS_MOVEMENT_NOTE,
            "variation_basis_label": VARIATION_BASIS_LABEL,
            "periods": [],
            "current": None,
            "previous": None,
            "banks": [],
            "unclassified_codes": [],
            "catalog_note": None,
        }

    by_period: dict[str, list[ItfRecord]] = {}
    for record in records:
        by_period.setdefault(record.period, []).append(record)

    periods = []
    prev: dict[str, float] | None = None
    for period in sorted(by_period.keys())[-months:]:
        row = _period_row(period, by_period[period], prev)
        periods.append(row)
        prev = {
            "inflow": row["inflow_base"] or 0.0,
            "outflow": row["outflow_base"] or 0.0,
            "gross": row["gross_movement"] or 0.0,
        }

    all_banks = sorted({
        r.declarant_name or r.declarant_ruc
        for r in records if r.declarant_name or r.declarant_ruc
    })
    unclassified_codes = sorted({
        normalize_code(r.operation_code)
        for r in records
        if r.operation_code and normalize_code(r.operation_code) not in OPERATION_CATALOG
    })

    return {
        "meaning": MEANING,
        "gross_movement_note": GROSS_MOVEMENT_NOTE,
        "variation_basis_label": VARIATION_BASIS_LABEL,
        "periods": periods,
        "current": periods[-1] if periods else None,
        "previous": periods[-2] if len(periods) > 1 else None,
        "banks": all_banks,
        "unclassified_codes": unclassified_codes,
        "catalog_note": (
            "Códigos 12 y 13 se reportan como acreditaciones; 14 y 15, como "
            "débitos. Los códigos "
            + ", ".join(unclassified_codes)
            + " no están en el catálogo verificado y se muestran sin "
            "clasificar; no se infiere su sentido."
            if unclassified_codes else
            "Códigos 12 y 13 se reportan como acreditaciones (entradas); "
            "14 y 15, como débitos (salidas)."
        ),
    }
