"""Estado de Resultados de Finanzas contra el declarado en la DJ anual (710).

Finanzas construye el Estado de Resultados desde comprobantes, planilla y
registros manuales; la DJ anual es lo que la contabilidad cerró y firmó ante
SUNAT. No tienen por qué coincidir —la diferencia son adiciones, deducciones,
depreciación, provisiones y ajustes que solo ve el contador—, pero mirarlas
lado a lado dice cuánto pesa eso y en qué línea. La diferencia se muestra
como tal; nunca como error de una de las dos partes.

Signos: Finanzas guarda los gastos en negativo y el 710 todo en positivo con
casillas separadas para utilidad y pérdida; aquí todo se lleva al criterio de
Finanzas (gasto negativo, pérdida negativa).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .renta_anual import con_nombre, vigentes

# (código de línea de Finanzas, nombre, cómo se arma desde las casillas con nombre)
# Cada entrada: lista de (nombre_casilla, signo).
MAPEO: list[tuple[str, list[tuple[str, int]]]] = [
    ("NET_SALES", [("ventas_netas", 1)]),
    ("COST_OF_SALES_LINE", [("costo_de_ventas", -1)]),
    ("GROSS_PROFIT", [("utilidad_bruta", 1), ("perdida_bruta", -1)]),
    ("ADMIN_EXPENSES_LINE", [("gastos_de_administracion", -1)]),
    ("SELLING_EXPENSES_LINE", [("gastos_de_ventas", -1)]),
    ("OPERATING_PROFIT", [("utilidad_operativa", 1), ("perdida_operativa", -1)]),
    ("OTHER_INCOME_LINE", [("otros_ingresos", 1)]),
    ("FINANCIAL_INCOME_LINE", [("ingresos_financieros", 1)]),
    ("FINANCIAL_EXPENSES_LINE", [("gastos_financieros", -1)]),
    ("PRE_TAX_PROFIT", [("utilidad_antes_de_impuesto", 1), ("perdida_antes_de_impuesto", -1)]),
    ("INCOME_TAX_LINE", [("impuesto_a_la_renta_gasto", -1)]),
    ("NET_INCOME", [("utilidad_neta", 1), ("perdida_neta", -1)]),
]

NOTAS = {
    "INCOME_TAX_LINE": "En Finanzas son los pagos a cuenta del 621; en el 710 es el impuesto del ejercicio (casilla 490).",
    "COST_OF_SALES_LINE": "Depende de cómo se categorizaron los comprobantes recibidos: costo o gasto administrativo.",
}


def _declarado(casillas: dict[str, Decimal | None], partes: list[tuple[str, int]]) -> Decimal | None:
    valores = [(casillas.get(nombre), signo) for nombre, signo in partes]
    if all(v is None for v, _ in valores):
        return None
    return sum((v * signo for v, signo in valores if v is not None), Decimal(0))


def cruce_estado_resultados(account_ruc: str, year: int) -> dict[str, Any]:
    """Filas comparables para un ejercicio; ``declaracion`` es None si no hay DJ."""
    from financials.services.statements import income_statement

    decl = vigentes(account_ruc).get(str(year))
    estado = income_statement(account_ruc, year)
    por_codigo = {l["code"]: l for l in estado["lines"]}
    casillas = con_nombre(decl.casillas) if decl else {}

    filas = []
    for codigo, partes in MAPEO:
        linea = por_codigo.get(codigo)
        if linea is None:
            continue
        finanzas = Decimal(str(linea["total"]))
        declarado = _declarado(casillas, partes) if decl else None
        diferencia = None if declarado is None else declarado - finanzas
        pct = None
        if diferencia is not None and finanzas:
            pct = float((diferencia / abs(finanzas)) * 100)
        filas.append({
            "code": codigo,
            "name": linea["name"],
            "line_type": linea["line_type"],
            "finanzas": float(finanzas),
            "declarado": None if declarado is None else float(declarado),
            "diferencia": None if diferencia is None else float(diferencia),
            "diferencia_pct": None if pct is None else round(pct, 1),
            "nota": NOTAS.get(codigo, ""),
        })
    return {
        "year": year,
        "declaracion": None if decl is None else {
            "nro_orden": decl.nro_orden,
            "fecha_presentacion": decl.fecha_presentacion,
            "rectificatoria": decl.rectificatoria,
            "tipo_declaracion": decl.tipo_declaracion,
        },
        "rows": filas,
    }
