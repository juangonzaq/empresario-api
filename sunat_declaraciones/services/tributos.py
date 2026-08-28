"""Qué paga una boleta, leído de su constancia.

SUNAT identifica cada tributo con un código de cuatro dígitos. Los que
importan para leer una boleta: 1011 IGV; renta de 3.ª según régimen —3031
general, 3111 RER, 3121 MYPE Tributario— y 3081 la regularización anual;
3042/3052 retenciones de 4.ª y 5.ª; 5210 EsSalud; 5310 ONP; 8021
fraccionamiento; 6xxx multas. Se clasifica por prefijo para no depender de
la lista completa (verificado con boletas reales 2023-2026).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

CLASES = {
    "igv": "IGV",
    "renta": "Renta 3.ª (pago a cuenta)",
    "retenciones": "Retenciones de renta",
    "essalud": "EsSalud",
    "onp": "ONP",
    "fraccionamiento": "Fraccionamiento",
    "multa": "Multa / intereses",
    "otro": "Otro tributo",
}


def clase_de(codigo: str) -> str:
    codigo = (codigo or "").strip()
    if codigo.startswith("10"):
        return "igv"
    if codigo in ("3042", "3052", "3062", "3072"):
        return "retenciones"
    if codigo[:2] in ("30", "31", "32"):
        return "renta"
    if codigo.startswith("52"):
        return "essalud"
    if codigo.startswith("53"):
        return "onp"
    if codigo.startswith("80"):
        return "fraccionamiento"
    if codigo.startswith("6"):
        return "multa"
    return "otro"


def tributos_de(constancia: dict[str, Any] | None) -> list[dict[str, Any]]:
    """[{codigo, descripcion, clase, importe}] de una constancia; vacío si no hay."""
    salida = []
    for t in (constancia or {}).get("tributos") or []:
        codigo = str(t.get("codTri") or "").strip()
        importe = t.get("mtoPagtot")
        periodo = str(t.get("perTri") or "").strip()
        salida.append({
            "codigo": codigo,
            "descripcion": str(t.get("descCodTri") or "").strip(),
            "clase": clase_de(codigo),
            "importe": Decimal(str(importe)) if importe is not None else None,
            # Periodo tributario que salda ese tributo (AAAAMM), si la constancia lo trae.
            "periodo": periodo if len(periodo) == 6 else "",
        })
    return salida


def pagado_por_clase(constancia: dict[str, Any] | None) -> dict[str, Decimal]:
    total: dict[str, Decimal] = {}
    for t in tributos_de(constancia):
        if t["importe"] is not None:
            total[t["clase"]] = total.get(t["clase"], Decimal(0)) + t["importe"]
    return total
