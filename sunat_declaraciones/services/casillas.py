"""Las casillas del F.V. 621 que importan, con nombre propio.

SUNAT devuelve el formulario como ``{"C100": "24141.00 ", ...}``. Las casillas
son las de siempre del PDT 621: 100/101 ventas gravadas y su IGV; 107/108,
110/111 y 113/114 compras según destino; 140 impuesto resultante; 184 tributo
a pagar de IGV; 188 importe a pagar de IGV; 301 ingresos netos; 312 pago a
cuenta de renta; 324 total a pagar de renta. Lo que no está aquí se conserva
en ``casillas`` tal cual, sin interpretar.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def numero(casillas: dict, *claves: str) -> Decimal | None:
    """Suma de las casillas pedidas; None si ninguna existe."""
    total: Decimal | None = None
    for clave in claves:
        valor = casillas.get(clave)
        if valor is None:
            continue
        try:
            cantidad = Decimal(str(valor).strip().replace(",", "") or "0")
        except InvalidOperation:
            continue
        total = cantidad if total is None else total + cantidad
    return total


def resumen_621(casillas: dict) -> dict:
    """Lo declarado en un 621, en las mismas magnitudes que `DeclaredSummary`."""
    igv_a_pagar = numero(casillas, "C184")
    if igv_a_pagar is None:
        igv_a_pagar = numero(casillas, "C140")
    renta = numero(casillas, "C312")
    igv_importe = numero(casillas, "C188")
    renta_total = numero(casillas, "C324")
    total = None
    if igv_importe is not None or renta_total is not None:
        total = (igv_importe or Decimal(0)) + (renta_total or Decimal(0))
    return {
        "ventas_base": numero(casillas, "C100"),
        "ventas_igv": numero(casillas, "C101"),
        "compras_base": numero(casillas, "C107", "C110", "C113"),
        "compras_igv": numero(casillas, "C108", "C111"),
        "igv_a_pagar": igv_a_pagar,
        "renta_ingresos": numero(casillas, "C301"),
        "renta_pago_a_cuenta": renta,
        "total_a_pagar": total,
    }
