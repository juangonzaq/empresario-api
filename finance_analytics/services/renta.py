"""Estimador del Impuesto a la Renta de tercera categoría.

Lee lo que ya tenemos —comprobantes emitidos y recibidos, más los registros
manuales— y lo traduce a lo que SUNAT va a cobrar según el régimen de la
empresa. Es una **estimación referencial**: la base real la determina la
contabilidad (gastos deducibles, depreciación, adiciones); aquí se usa ventas
netas menos compras netas, ambas sin IGV.

Reglas (Perú, renta empresarial):

* **RMT**: escalonado sobre la utilidad anual: hasta 15 UIT al 10 %; el
  exceso al 29,5 %. Pagos a cuenta mensuales del 1 % de los ingresos netos
  mientras los ingresos anuales no superen 300 UIT; pasado eso, 1,5 % (o el
  coeficiente del año anterior, que aquí no se conoce).
* **RG**: 29,5 % sobre la utilidad anual. Pagos a cuenta del 1,5 % de los
  ingresos netos mensuales (o coeficiente).
* **RER**: 1,5 % de los ingresos netos de cada mes, y es pago definitivo: no
  hay regularización anual.
* **RUS**: cuota fija mensual por categoría (S/ 20 hasta S/ 5 000 de
  ingresos o compras; S/ 50 hasta S/ 8 000).

Sin régimen declarado se asume RMT —el más común entre las empresas que usan
Empresario— y se dice en voz alta en la respuesta.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.utils import timezone

from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

from .common import money
from .cpe_summary import _igv, document_amount

UIT = {2023: Decimal("4950"), 2024: Decimal("5150"), 2025: Decimal("5350"), 2026: Decimal("5500")}
IGV = Decimal("1.18")
TRAMO_RMT_UIT = 15
LIMITE_PAGO_1PCT_UIT = 300
TASA_RMT_1 = Decimal("0.10")
TASA_GENERAL = Decimal("0.295")
TASA_RER = Decimal("0.015")
TASA_PAC_1 = Decimal("0.01")
TASA_PAC_15 = Decimal("0.015")
RUS_CATEGORIAS = [(Decimal("5000"), Decimal("20")), (Decimal("8000"), Decimal("50"))]

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

REGIME_LABEL = {
    "RUS": "Nuevo RUS", "RER": "Régimen Especial", "RMT": "MYPE Tributario", "RG": "Régimen General",
}


def uit_for(year: int) -> Decimal:
    return UIT.get(year) or UIT[max(UIT)]


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ------------------------------------------------------------ base mensual

def payroll_cost_by_period(account_ruc: str, year: int) -> dict[str, Decimal]:
    """Costo de personal deducible por mes: haberes (EARNING) más aportes del
    empleador (EMPLOYER_COST). No incluye los descuentos, que salen del haber y
    ya están dentro de él. Solo periodos de planilla no-borrador."""
    from django.db.models import Sum

    from payroll.models import PayrollEntryLine, PayrollStatus

    rows = (
        PayrollEntryLine.objects
        .filter(
            entry__period__taxpayer_id=account_ruc,
            entry__period__year=year,
            concept__kind__in=["earning", "employer_cost"],
        )
        .exclude(entry__period__status=PayrollStatus.DRAFT)
        .values("entry__period__month")
        .annotate(total=Sum("amount"))
    )
    out: dict[str, Decimal] = {}
    for r in rows:
        period = f"{year}{r['entry__period__month']:02d}"
        out[period] = (out.get(period, Decimal("0")) + (r["total"] or Decimal("0")))
    return out


def monthly_bases(account_ruc: str, year: int, manual: list[Any]) -> list[dict[str, Any]]:
    """Ingresos y gastos sin IGV por mes del año, en PEN, con el detalle de qué
    compone cada lado: comprobantes, registros manuales y planilla."""
    docs = (
        ElectronicInvoice.objects.for_account(account_ruc)
        .filter(period__startswith=str(year), is_cancelled=False, is_rejected=False)
        .select_related("extract", "override").defer("xml_content", "raw")
    )
    ing_cpe: dict[str, Decimal] = defaultdict(Decimal)
    ing_man: dict[str, Decimal] = defaultdict(Decimal)
    gas_cpe: dict[str, Decimal] = defaultdict(Decimal)
    gas_man: dict[str, Decimal] = defaultdict(Decimal)
    for doc in docs:
        if (doc.currency or "PEN") != "PEN" and doc.currency_symbol not in ("S/", "S/.", ""):
            continue  # sin tipo de cambio no se suma
        total = document_amount(doc)
        igv = _igv(doc)
        base = total - igv if igv is not None else (total / IGV)
        if doc.document_class == DocumentClass.CREDIT_NOTE:
            base = -base
        (ing_cpe if doc.direction == Direction.ISSUED else gas_cpe)[doc.period] += base
    for e in manual:
        if (e.currency or "PEN") != "PEN" or not str(e.period).startswith(str(year)):
            continue
        (ing_man if e.direction == Direction.ISSUED else gas_man)[e.period] += Decimal(e.amount)
    planilla = payroll_cost_by_period(account_ruc, year)

    rows = []
    for m in range(1, 13):
        period = f"{year}{m:02d}"
        comp = {
            "ingresos_comprobantes": _q(ing_cpe.get(period, Decimal("0"))),
            "ingresos_manuales": _q(ing_man.get(period, Decimal("0"))),
            "gastos_comprobantes": _q(gas_cpe.get(period, Decimal("0"))),
            "gastos_manuales": _q(gas_man.get(period, Decimal("0"))),
            "gastos_planilla": _q(planilla.get(period, Decimal("0"))),
        }
        ingresos = comp["ingresos_comprobantes"] + comp["ingresos_manuales"]
        gastos = comp["gastos_comprobantes"] + comp["gastos_manuales"] + comp["gastos_planilla"]
        con_datos = any(v for v in comp.values())
        rows.append({
            "period": period, "label": f"{MESES[m-1]} {year}", "month": m,
            "ingresos": _q(ingresos), "gastos": _q(gastos), "utilidad": _q(ingresos - gastos),
            "con_datos": con_datos, "components": comp,
        })
    return rows


# ----------------------------------------------------------------- escala

def tramos_rmt(utilidad: Decimal, uit: Decimal) -> list[dict[str, Any]]:
    limite = uit * TRAMO_RMT_UIT
    base1 = max(Decimal("0"), min(utilidad, limite))
    base2 = max(Decimal("0"), utilidad - limite)
    return [
        {"label": f"Hasta 15 UIT (S/ {money(limite):,.0f})", "desde": Decimal("0"), "hasta": limite,
         "tasa": TASA_RMT_1, "base": _q(base1), "impuesto": _q(base1 * TASA_RMT_1)},
        {"label": "Exceso de 15 UIT", "desde": limite, "hasta": None,
         "tasa": TASA_GENERAL, "base": _q(base2), "impuesto": _q(base2 * TASA_GENERAL)},
    ]


def tramos_rg(utilidad: Decimal) -> list[dict[str, Any]]:
    base = max(Decimal("0"), utilidad)
    return [{"label": "Toda la utilidad", "desde": Decimal("0"), "hasta": None,
             "tasa": TASA_GENERAL, "base": _q(base), "impuesto": _q(base * TASA_GENERAL)}]


def impuesto_anual(regime: str, utilidad: Decimal, uit: Decimal) -> dict[str, Any]:
    tramos = tramos_rmt(utilidad, uit) if regime == "RMT" else tramos_rg(utilidad)
    impuesto = sum((t["impuesto"] for t in tramos), Decimal("0"))
    base = max(Decimal("0"), utilidad)
    return {
        "base": _q(base), "tramos": tramos, "impuesto": _q(impuesto),
        "tasa_efectiva": float(_q(impuesto / base * 100)) if base > 0 else 0.0,
    }


def pago_a_cuenta(regime: str, ingresos_mes: Decimal, ingresos_anual_proy: Decimal, uit: Decimal) -> tuple[Decimal, Decimal]:
    """(tasa, importe) del pago a cuenta del mes según régimen."""
    if regime == "RER":
        return TASA_RER, _q(max(Decimal("0"), ingresos_mes) * TASA_RER)
    if regime == "RMT":
        tasa = TASA_PAC_1 if ingresos_anual_proy <= uit * LIMITE_PAGO_1PCT_UIT else TASA_PAC_15
        return tasa, _q(max(Decimal("0"), ingresos_mes) * tasa)
    if regime == "RG":
        return TASA_PAC_15, _q(max(Decimal("0"), ingresos_mes) * TASA_PAC_15)
    return Decimal("0"), Decimal("0")


def cuota_rus(ingresos_mes: Decimal, gastos_mes: Decimal) -> Decimal | None:
    ref = max(ingresos_mes, gastos_mes)
    for tope, cuota in RUS_CATEGORIAS:
        if ref <= tope:
            return cuota
    return None  # supera el RUS


# ------------------------------------------------------------------ resumen

RUS_TOPE_ANUAL = Decimal("96000")
RUS_TOPE_MENSUAL = Decimal("8000")
RER_TOPE_ANUAL = Decimal("525000")


def regime_mismatch(regime: str, rows: list[dict[str, Any]]) -> str | None:
    """Por qué el régimen declarado no cuadra con los números, o None."""
    ing = sum((r["ingresos"] for r in rows), Decimal("0"))
    gas = sum((r["gastos"] for r in rows), Decimal("0"))
    if regime == "RUS":
        if ing > RUS_TOPE_ANUAL or gas > RUS_TOPE_ANUAL:
            return f"Tus ingresos o compras del año superan el tope del Nuevo RUS (S/ {RUS_TOPE_ANUAL:,.0f})."
        if any(r["ingresos"] > RUS_TOPE_MENSUAL or r["gastos"] > RUS_TOPE_MENSUAL for r in rows):
            return f"Algún mes supera el tope mensual del Nuevo RUS (S/ {RUS_TOPE_MENSUAL:,.0f})."
    if regime == "RER" and ing > RER_TOPE_ANUAL:
        return f"Tus ingresos del año superan el tope del RER (S/ {RER_TOPE_ANUAL:,.0f})."
    return None


def _projection_inputs(account_ruc: str, year: int):
    """(overrides dict, set of closed periods) for this company and year."""
    from finance_analytics.models import FinancePeriodClose, RentaProjection

    ov = RentaProjection.objects.filter(account_ruc=account_ruc, year=year).first()
    overrides = {
        "monthly_sales": ov.monthly_sales if ov else None,
        "monthly_expenses": ov.monthly_expenses if ov else None,
        "monthly_payroll": ov.monthly_payroll if ov else None,
        "note": ov.note if ov else "",
    }
    closed = set(FinancePeriodClose.objects.filter(account_ruc=account_ruc, period__startswith=str(year)).values_list("period", flat=True))
    return overrides, closed


def _pagos_a_cuenta_declarados(account_ruc: str, year: int) -> dict[str, Decimal]:
    """Periodo → pago a cuenta declarado en el 621 vigente (casilla 312)."""
    try:
        from sunat_declaraciones.services.casillas import resumen_621
        from sunat_declaraciones.services.sync import vigentes_621
    except Exception:  # pragma: no cover - app siempre presente
        return {}
    out: dict[str, Decimal] = {}
    for period, decl in vigentes_621(account_ruc).items():
        if not period.startswith(str(year)):
            continue
        pago = resumen_621(decl.casillas)["renta_pago_a_cuenta"]
        if pago is not None:
            out[period] = _q(pago)
    return out


def _dj_anual_declarada(account_ruc: str, year: int) -> dict[str, Any] | None:
    """La DJ anual presentada de ese ejercicio, si e-renta ya la trajo."""
    try:
        from sunat_declaraciones.services import resumen_anual
    except Exception:  # pragma: no cover
        return None
    return next((e for e in resumen_anual(account_ruc) if e["ejercicio"] == str(year)), None)


def renta_summary(account_ruc: str, regime: str, year: int, manual: list[Any]) -> dict[str, Any]:
    regime_declared = regime if regime in REGIME_LABEL else ""
    regime_assumed = not regime_declared
    uit = uit_for(year)
    rows = monthly_bases(account_ruc, year, manual)
    # Si el régimen declarado no aguanta estos números, la estimación útil es
    # la del MYPE Tributario (adonde pasaría la empresa): se calcula con ese y
    # se dice claramente.
    mismatch = regime_mismatch(regime_declared, rows) if regime_declared else None
    reg = "RMT" if (regime_assumed or mismatch) else regime_declared
    hoy = timezone.localdate()
    meses_transcurridos = 12 if year < hoy.year else (hoy.month if year == hoy.year else 0)
    con_datos = [r for r in rows if r["con_datos"]]
    overrides, closed = _projection_inputs(account_ruc, year)

    # Proyección a diciembre = actuals firmes + proyección de los meses que
    # faltan. Los meses **cerrados** son hechos y no se re-estiman; solo se
    # proyectan los abiertos. La base mensual de cada componente puede venir
    # editada por el usuario; si no, se calcula sola. La planilla se trata como
    # costo recurrente (promedio de sus meses cargados).
    meses_base = max(1, min(meses_transcurridos, 12)) if year == hoy.year else 12
    remaining = max(0, 12 - meses_transcurridos)

    def _sum(field: str) -> Decimal:
        return sum((r["components"][field] for r in rows), Decimal("0"))

    def _auto_basis(field: str, recurring: bool) -> Decimal:
        total = _sum(field)
        if recurring:
            months = sum(1 for r in rows if r["components"][field] > 0)
            return _q(total / months) if months else Decimal("0")
        return _q(total / meses_base)

    def _project(field: str, recurring: bool, override) -> tuple[Decimal, Decimal, Decimal]:
        """(full_year, basis_used, auto_basis) for one component."""
        actual = _sum(field)
        auto = _auto_basis(field, recurring)
        basis = Decimal(override) if override is not None else auto
        return _q(actual + basis * remaining), _q(basis), auto

    ing_acum = sum((r["ingresos"] for r in rows), Decimal("0"))
    gas_acum = sum((r["gastos"] for r in rows), Decimal("0"))
    uti_acum = ing_acum - gas_acum

    sales_full, sales_basis, sales_auto = _project("ingresos_comprobantes", False, overrides["monthly_sales"])
    man_in_full, _, _ = _project("ingresos_manuales", False, None)
    cpe_full, exp_basis, exp_auto = _project("gastos_comprobantes", False, overrides["monthly_expenses"])
    man_ex_full, _, _ = _project("gastos_manuales", False, None)
    planilla_proy, pay_basis, pay_auto = _project("gastos_planilla", True, overrides["monthly_payroll"])

    ing_proy = _q(sales_full + man_in_full)
    gas_proy = _q(cpe_full + man_ex_full + planilla_proy)
    uti_proy = _q(ing_proy - gas_proy)

    assumptions = {
        "editable": remaining > 0,
        "remaining_months": remaining,
        "note": overrides["note"],
        "sales": {"override": overrides["monthly_sales"], "auto": sales_auto, "used": sales_basis},
        "expenses": {"override": overrides["monthly_expenses"], "auto": exp_auto, "used": exp_basis},
        "payroll": {"override": overrides["monthly_payroll"], "auto": pay_auto, "used": pay_basis},
    }

    for r in rows:
        r["closed"] = r["period"] in closed

    # Lo que de verdad se declaró como pago a cuenta en el 621 de cada mes
    # (casilla 312), cuando la consulta de declaraciones ya lo trajo. Es el
    # contraste de la estimación: si difieren, el que manda es SUNAT.
    declarados = _pagos_a_cuenta_declarados(account_ruc, year)
    pac_declarado_total = Decimal("0")
    for r in rows:
        r["pago_a_cuenta_declarado"] = declarados.get(r["period"])
        pac_declarado_total += declarados.get(r["period"]) or Decimal("0")

    pac_total = Decimal("0")
    for r in rows:
        if reg == "RUS":
            cuota = cuota_rus(r["ingresos"], r["gastos"]) if r["con_datos"] else Decimal("0")
            r["pago_a_cuenta"] = cuota if cuota is not None else None
            r["tasa_pago_a_cuenta"] = None
            r["supera_rus"] = cuota is None and r["con_datos"]
        else:
            tasa, imp = pago_a_cuenta(reg, r["ingresos"], ing_proy, uit)
            r["pago_a_cuenta"] = imp if r["con_datos"] else Decimal("0")
            r["tasa_pago_a_cuenta"] = float(tasa * 100)
        pac_total += r["pago_a_cuenta"] or Decimal("0")

    def _sum(field: str) -> Decimal:
        return sum((r["components"][field] for r in rows), Decimal("0"))

    breakdown = {
        "income": {
            "comprobantes": _q(_sum("ingresos_comprobantes")),
            "manuales": _q(_sum("ingresos_manuales")),
        },
        "expenses": {
            "comprobantes": _q(_sum("gastos_comprobantes")),
            "manuales": _q(_sum("gastos_manuales")),
            "planilla": _q(_sum("gastos_planilla")),
        },
    }
    payroll_months = sum(1 for r in rows if r["components"]["gastos_planilla"] > 0)

    out: dict[str, Any] = {
        "year": year, "uit": uit, "regime": reg, "regime_label": REGIME_LABEL[reg],
        "regime_assumed": regime_assumed,
        "breakdown": breakdown,
        "includes_payroll": payroll_months > 0,
        "payroll_months": payroll_months,
        "regime_declared": regime_declared or None,
        "regime_declared_label": REGIME_LABEL.get(regime_declared),
        "regime_mismatch": mismatch,
        "meses_con_datos": len(con_datos), "meses_base": meses_base,
        "closed_periods": sorted(closed),
        "assumptions": assumptions,
        "months": rows,
        # Lo que se le declaró a SUNAT en la DJ anual de este ejercicio: el
        # cierre real contra el que comparar la estimación de arriba.
        "dj_anual": _dj_anual_declarada(account_ruc, year),
        "totals": {
            "ingresos": _q(ing_acum), "gastos": _q(gas_acum), "utilidad": _q(uti_acum),
            "pagos_a_cuenta": _q(pac_total),
            "pagos_a_cuenta_declarados": _q(pac_declarado_total),
            "meses_declarados": len(declarados),
        },
        "proyeccion": {
            "ingresos": ing_proy, "gastos": gas_proy, "utilidad": uti_proy,
            "planilla": planilla_proy,
        },
        "notes": [
            "Como gasto se consideran los comprobantes recibidos, tus registros manuales y el costo de planilla (haberes + aportes del empleador), todo sin IGV. La base real la determina la contabilidad (depreciación, adiciones y otras deducciones).",
        ],
    }
    if payroll_months and payroll_months < meses_base:
        out["notes"].append(f"El costo de planilla se cuenta solo en {payroll_months} de {meses_base} meses (los que ya tienen planilla calculada); genera las planillas faltantes para afinar la utilidad.")
    elif not payroll_months:
        out["notes"].append("Aún no hay planilla calculada este año, así que el gasto de personal no está en la estimación. Genera tus planillas en Colaboradores para incluirlo.")
    if regime_assumed:
        out["notes"].insert(0, "Tu empresa no declaró su régimen: se asume MYPE Tributario. Puedes fijarlo aquí mismo o en Obligaciones › Calendario.")
    if mismatch:
        out["notes"].insert(0, f"{mismatch} Tu empresa figura en {REGIME_LABEL[regime_declared]}; la estimación se hace como MYPE Tributario, que es adonde pasaría. Revisa el régimen declarado.")

    if reg in ("RMT", "RG"):
        acum = impuesto_anual(reg, uti_acum, uit)
        proy = impuesto_anual(reg, uti_proy, uit)
        out["annual"] = {
            "acumulado": acum, "proyectado": proy,
            "saldo_estimado": _q(proy["impuesto"] - pac_total),
            "umbral_tramo": _q(uit * TRAMO_RMT_UIT) if reg == "RMT" else None,
            "umbral_300_uit": _q(uit * LIMITE_PAGO_1PCT_UIT),
        }
        out["escala"] = (
            [{"label": "Hasta 15 UIT", "tasa": 10.0}, {"label": "Más de 15 UIT", "tasa": 29.5}]
            if reg == "RMT" else [{"label": "Toda la utilidad", "tasa": 29.5}]
        )
        if reg == "RMT":
            out["notes"].append("Pagos a cuenta: 1 % de los ingresos netos del mes mientras los ingresos del año no superen 300 UIT; luego 1,5 % (o coeficiente).")
        else:
            out["notes"].append("Pagos a cuenta: 1,5 % de los ingresos netos del mes o el coeficiente del ejercicio anterior, el mayor.")
    elif reg == "RER":
        out["annual"] = None
        out["escala"] = [{"label": "Ingresos netos del mes", "tasa": 1.5}]
        out["notes"].append("En el RER el 1,5 % mensual es pago definitivo: no hay regularización anual. Límite de ingresos: S/ 525 000 al año.")
        if ing_proy > Decimal("525000"):
            out["notes"].append("Tus ingresos proyectados superan el límite del RER: tocaría pasar a MYPE Tributario.")
    else:  # RUS
        out["annual"] = None
        out["escala"] = [{"label": "Hasta S/ 5 000", "cuota": 20}, {"label": "Hasta S/ 8 000", "cuota": 50}]
        out["notes"].append("El Nuevo RUS paga una cuota fija mensual según ingresos o compras del mes; no declara renta anual.")
    return out
