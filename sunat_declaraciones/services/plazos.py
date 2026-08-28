"""Vencimiento de la declaración mensual de un periodo, según el cronograma
que ya usa el calendario (``sensor_sunat/data/cronograma_<año>.yaml``).

Solo se conoce para los años con cronograma cargado: para el resto se devuelve
None y quien pregunta debe decir «sin cronograma», nunca «a tiempo».
"""

from __future__ import annotations

from datetime import date

from sensor_sunat.calendario import COL, DATA


def vencimiento_de(ruc: str, periodo: str, buen_contribuyente: bool = False) -> date | None:
    fechas = DATA.get("mensual", {}).get(periodo)
    if not fechas:
        return None
    col = 6 if buen_contribuyente else COL[int(ruc[-1])]
    try:
        return date.fromisoformat(fechas[col])
    except (IndexError, ValueError):
        return None


def periodo_anterior(periodo: str) -> str:
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    return f"{anio - 1}12" if mes == 1 else f"{anio}{mes - 1:02d}"


def periodo_siguiente(periodo: str) -> str:
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    return f"{anio + 1}01" if mes == 12 else f"{anio}{mes + 1:02d}"


def ultimo_periodo_cerrado(hoy: date) -> str:
    """El periodo cuya declaración toca ahora: el mes pasado."""
    return periodo_anterior(f"{hoy.year}{hoy.month:02d}")


def fin_de_mes(periodo: str) -> date:
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    siguiente = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    return date.fromordinal(siguiente.toordinal() - 1)
