"""Consultas a la base que el asistente ejecuta según la pregunta.

El modelo NO escribe SQL: elige entre estas consultas parametrizadas, y el RUC
lo fija el backend en cada una — así una pregunta jamás puede leer datos de
otra empresa. Los importes reutilizan los mismos ayudantes que el tablero
financiero (``document_amount``, notas de crédito restando), para que el
asistente nunca dé un número distinto del que muestra la pantalla.

Cada función devuelve dicts JSON-serializables, con montos separados por
moneda: sumar PEN y USD es una de las prohibiciones del prompt.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

from finance_analytics.services.cpe_summary import (
    document_amount, document_counterparty,
)
from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

PERIODO = re.compile(r"^\d{6}$")
# Tope de la ventana consultable de una vez: suficiente para comparar tres
# ejercicios sin dejar que una consulta cargue el histórico entero.
MAX_MESES = 36
MAX_LIMITE = 20


def _validar_rango(desde: str, hasta: str) -> tuple[str, str]:
    if not (PERIODO.match(desde or "") and PERIODO.match(hasta or "")):
        raise ValueError("Los periodos van como YYYYMM, p. ej. 202401.")
    if desde > hasta:
        desde, hasta = hasta, desde
    meses = (int(hasta[:4]) - int(desde[:4])) * 12 + int(hasta[4:]) - int(desde[4:]) + 1
    if meses > MAX_MESES:
        raise ValueError(f"El rango no puede superar {MAX_MESES} meses.")
    return desde, hasta


def _documentos(taxpayer_id: str, direction: str, desde: str, hasta: str):
    return (
        ElectronicInvoice.objects.for_account(taxpayer_id)
        .filter(direction=direction, period__gte=desde, period__lte=hasta)
        .select_related("extract", "override")
        .defer("xml_content", "raw")
    )


def _por_mes(taxpayer_id: str, direction: str, desde: str, hasta: str) -> dict[str, Any]:
    desde, hasta = _validar_rango(desde, hasta)
    buckets: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"facturado": Decimal("0"), "notas_credito": Decimal("0"),
                 "comprobantes": 0}
    )
    for doc in _documentos(taxpayer_id, direction, desde, hasta):
        b = buckets[(doc.period, doc.currency or "PEN")]
        amount = document_amount(doc)
        if doc.document_class == DocumentClass.CREDIT_NOTE:
            b["notas_credito"] += amount
        else:
            b["facturado"] += amount
            b["comprobantes"] += 1
    meses = [
        {
            "periodo": periodo, "moneda": moneda,
            "facturado": str(b["facturado"]),
            "notas_credito": str(b["notas_credito"]),
            "neto": str(b["facturado"] - b["notas_credito"]),
            "comprobantes": b["comprobantes"],
        }
        for (periodo, moneda), b in sorted(buckets.items())
    ]
    return {"desde": desde, "hasta": hasta, "meses": meses,
            "nota": "neto = facturado − notas de crédito; monedas separadas."}


def ventas_por_mes(taxpayer_id: str, desde: str, hasta: str) -> dict[str, Any]:
    """Facturación emitida por mes (venta facturada, no cobranza)."""
    return _por_mes(taxpayer_id, Direction.ISSUED, desde, hasta)


def compras_por_mes(taxpayer_id: str, desde: str, hasta: str) -> dict[str, Any]:
    """Comprobantes recibidos por mes."""
    return _por_mes(taxpayer_id, Direction.RECEIVED, desde, hasta)


def top_contrapartes(
    taxpayer_id: str, direccion: str, desde: str, hasta: str, limite: int = 5,
) -> dict[str, Any]:
    """Clientes (ventas) o proveedores (compras) con mayor neto en el rango."""
    if direccion not in ("ventas", "compras"):
        raise ValueError("direccion debe ser «ventas» o «compras».")
    desde, hasta = _validar_rango(desde, hasta)
    limite = max(1, min(int(limite), MAX_LIMITE))
    direction = Direction.ISSUED if direccion == "ventas" else Direction.RECEIVED

    buckets: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"neto": Decimal("0"), "comprobantes": 0}
    )
    for doc in _documentos(taxpayer_id, direction, desde, hasta):
        nombre = document_counterparty(doc, direction) or "(sin nombre)"
        b = buckets[(nombre, doc.currency or "PEN")]
        amount = document_amount(doc)
        if doc.document_class == DocumentClass.CREDIT_NOTE:
            b["neto"] -= amount
        else:
            b["neto"] += amount
            b["comprobantes"] += 1
    filas = sorted(buckets.items(), key=lambda kv: kv[1]["neto"], reverse=True)
    return {
        "direccion": direccion, "desde": desde, "hasta": hasta,
        "contrapartes": [
            {"nombre": nombre, "moneda": moneda,
             "neto": str(b["neto"]), "comprobantes": b["comprobantes"]}
            for (nombre, moneda), b in filas[:limite]
        ],
    }


# ── Especificación para el modelo ──

_RANGO_PROPS = {
    "desde": {"type": "string", "pattern": "^[0-9]{6}$",
              "description": "Periodo inicial YYYYMM, p. ej. 202401"},
    "hasta": {"type": "string", "pattern": "^[0-9]{6}$",
              "description": "Periodo final YYYYMM"},
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ventas_por_mes",
            "description": (
                "Facturación EMITIDA por mes entre dos periodos, separada por "
                "moneda. Las notas de crédito restan. Es venta facturada, no "
                "cobranza. Úsala para tendencias o comparaciones entre "
                "periodos/años que no estén en el contexto."
            ),
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": _RANGO_PROPS,
                "required": ["desde", "hasta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compras_por_mes",
            "description": (
                "Comprobantes RECIBIDOS (compras) por mes entre dos periodos, "
                "separados por moneda. Las notas de crédito restan."
            ),
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": _RANGO_PROPS,
                "required": ["desde", "hasta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_contrapartes",
            "description": (
                "Clientes (ventas) o proveedores (compras) con mayor monto "
                "neto en un rango de periodos. Útil para explicar variaciones: "
                "qué cliente creció, qué proveedor pesa más."
            ),
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    **_RANGO_PROPS,
                    "direccion": {"type": "string", "enum": ["ventas", "compras"]},
                    "limite": {"type": "integer", "minimum": 1, "maximum": MAX_LIMITE},
                },
                "required": ["direccion", "desde", "hasta"],
            },
        },
    },
]


def executors_for(taxpayer_id: str) -> dict[str, Any]:
    """Las funciones con el RUC ya amarrado: lo único que el modelo puede
    elegir son los parámetros declarados en TOOL_SPECS."""
    return {
        "ventas_por_mes": lambda **kw: ventas_por_mes(taxpayer_id, **kw),
        "compras_por_mes": lambda **kw: compras_por_mes(taxpayer_id, **kw),
        "top_contrapartes": lambda **kw: top_contrapartes(taxpayer_id, **kw),
    }
