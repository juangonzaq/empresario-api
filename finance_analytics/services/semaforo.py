"""Semáforo de gastos sobre ingresos: personal, otros gastos y el total.

Tres filas, una base: los ingresos netos en soles del mes (facturación
emitida + ingresos manuales) valen 100 %.

* **Gastos de personal** — la masa salarial de la planilla registrada en
  Colaboradores **más el aporte EsSalud que paga la empresa** (9 % con base
  mínima la RMV, ambos configurables); no sale de los comprobantes, porque
  ni los sueldos ni EsSalud generan CPE. La fila lleva el desglose en
  columnas, pero el color se decide por el costo total del empleador, que es
  lo que mide la regla 25-35 %.
* **Otros gastos** — los comprobantes recibidos del mes más los gastos
  manuales (el neto de la pestaña Comprobantes recibidos).
* **Total** — la suma de ambos.

Cada fila se pinta verde, amarillo o rojo según los umbrales configurables
de ``THRESHOLDS`` (regla práctica del personal: verde hasta 28 %, amarillo
hasta 35 %, rojo de ahí en adelante). Como todo en este módulo, se dice la
base de cada porcentaje: facturar no es cobrar, y un semáforo sin base
invita a decidir sobre un número que no se entiende.
"""

from __future__ import annotations

from typing import Any

from .common import THRESHOLDS, period_label

BASIS = (
    "Porcentajes sobre los ingresos netos en soles del mes (facturación "
    "emitida + ingresos manuales). Facturar no es cobrar."
)


def _estado(pct: float | None, verde_hasta: float, amarillo_hasta: float) -> str:
    if pct is None:
        return "sin_base"
    if pct <= verde_hasta:
        return "verde"
    if pct <= amarillo_hasta:
        return "amarillo"
    return "rojo"


def _pct(amount: float, ingresos: float | None) -> float | None:
    if not ingresos or ingresos <= 0:
        return None
    return round(amount / ingresos * 100, 1)


def _net_pen(row: dict | None) -> float:
    if not row:
        return 0.0
    return row.get("by_currency", {}).get("PEN", {}).get("net") or 0.0


def _masa_salarial(account_ruc: str) -> tuple[float, float, int, int]:
    """(sueldos, essalud, con sueldo, sin sueldo) de la gente en planilla.

    EsSalud lo paga la empresa **además** del sueldo —no se descuenta al
    trabajador—, así que es gasto de personal por derecho propio. Se calcula
    por trabajador porque la base del aporte tiene piso: la RMV vigente,
    aunque el sueldo registrado sea menor.
    """
    from colaboradores.models import Colaborador

    tasa = THRESHOLDS["essalud_pct"] / 100
    rmv = THRESHOLDS["rmv_pen"]

    activos = Colaborador.objects.filter(taxpayer_id=account_ruc, is_active=True)
    sueldos = essalud = 0.0
    con_sueldo = sin_sueldo = 0
    for colaborador in activos:
        if colaborador.monthly_salary is None:
            sin_sueldo += 1
        else:
            con_sueldo += 1
            sueldo = float(colaborador.monthly_salary)
            sueldos += sueldo
            essalud += max(sueldo, rmv) * tasa
    return sueldos, essalud, con_sueldo, sin_sueldo


def _fila(
    key: str, label: str, amount: float, ingresos: float | None,
    detalle: str, breakdown: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    verde = THRESHOLDS[f"semaforo_{key}_verde_pct"]
    amarillo = THRESHOLDS[f"semaforo_{key}_amarillo_pct"]
    pct = _pct(amount, ingresos)
    return {
        "key": key,
        "label": label,
        "amount_pen": money_round(amount),
        "pct": pct,
        "estado": _estado(pct, verde, amarillo),
        "verde_hasta_pct": verde,
        "amarillo_hasta_pct": amarillo,
        "detalle": detalle,
        # Columnas dentro de la fila (sueldos | EsSalud): el estado se decide
        # por el total, pero cada componente se ve por separado.
        "breakdown": breakdown or [],
    }


def money_round(value: float) -> float:
    return round(value, 2)


def semaforo(
    account_ruc: str, sales: dict[str, Any], purchases: dict[str, Any],
) -> dict[str, Any]:
    """El semáforo del último mes con facturación.

    ``sales`` y ``purchases`` son las salidas de ``sales_summary`` y
    ``purchases_summary`` **con los registros manuales incluidos**: así los
    porcentajes cuadran con las tablas mensuales que el usuario ya ve.
    """
    period = sales.get("latest_period")
    ingresos = _net_pen(sales.get("current"))

    # Las compras del MISMO mes que los ingresos, no el último mes con
    # compras: un porcentaje que mezcla meses no es un porcentaje.
    compras = 0.0
    for row in purchases.get("periods") or []:
        if row.get("period") == period:
            compras = _net_pen(row)
            break

    sueldos, essalud, con_sueldo, sin_sueldo = _masa_salarial(account_ruc)
    personal = sueldos + essalud
    tasa_essalud = THRESHOLDS["essalud_pct"]

    avisos: list[str] = []
    if sin_sueldo:
        avisos.append(
            f"{sin_sueldo} colaborador(es) en planilla sin sueldo registrado: "
            "los gastos de personal están subestimados."
        )
    if ingresos <= 0:
        avisos.append(
            "Sin ingresos netos en soles este mes: no hay base para los "
            "porcentajes."
        )

    base = ingresos if ingresos > 0 else None
    rows = [
        _fila(
            "personal", "Gastos de personal", personal, base,
            f"Sueldos de {con_sueldo} colaborador(es) en planilla más el "
            f"aporte EsSalud ({tasa_essalud:g} %) que paga la empresa.",
            breakdown=[
                {"label": "Sueldos", "amount_pen": money_round(sueldos)},
                {
                    "label": f"EsSalud ({tasa_essalud:g} %)",
                    "amount_pen": money_round(essalud),
                },
            ],
        ),
        _fila(
            "otros", "Otros gastos", compras, base,
            "Comprobantes recibidos del mes más gastos manuales.",
        ),
        _fila(
            "total", "Total de gastos", personal + compras, base,
            "Personal (con EsSalud) + otros gastos, sobre los mismos ingresos.",
        ),
    ]

    return {
        "period": period,
        "label": period_label(period) if period else None,
        "basis": BASIS,
        "ingresos_pen": money_round(ingresos),
        "rows": rows,
        "avisos": avisos,
    }
