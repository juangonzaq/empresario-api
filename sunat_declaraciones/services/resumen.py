"""Lo que la pantalla de Finanzas enseña: un bloque por periodo con qué se
presentó, cuándo, si llegó a tiempo y cuánto se pagó."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from django.utils import timezone

from ..models import ConsultaDeclaraciones, DeclaracionPresentada, Formulario
from .casillas import resumen_621
from .plazos import ultimo_periodo_cerrado, vencimiento_de
from .tributos import CLASES, tributos_de

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _num(valor: Decimal | None) -> float | None:
    return None if valor is None else float(valor)


def etiqueta(periodo: str) -> str:
    mes = int(periodo[4:6])
    if mes == 13:
        return f"Anual {periodo[:4]}"
    return f"{MESES[mes - 1]} {periodo[:4]}"


def _entero(valor: Any) -> int | None:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _declaracion(decl: DeclaracionPresentada, vencimiento: date | None) -> dict[str, Any]:
    a_tiempo = None
    if vencimiento and decl.fecha_presentacion:
        a_tiempo = decl.fecha_presentacion <= vencimiento
    c = decl.constancia or {}
    return {
        "tributos": _tributos(decl),
        "forma_pago": c.get("formaPago") or "",
        "tipo_declaracion": c.get("tipoDeclaracion") or "",
        # Solo la PLAME lo trae; en el resto queda None.
        "trabajadores": _entero(c.get("trabajadores")) if c.get("form0601") or decl.formulario == Formulario.PLAME else None,
        "pensionistas": _entero(c.get("pensionistas")) if decl.formulario == Formulario.PLAME else None,
        "prestadores_4ta": _entero(c.get("personalCuarta")) if decl.formulario == Formulario.PLAME else None,
        "nro_orden": decl.nro_orden,
        "fecha_presentacion": decl.fecha_presentacion,
        "banco": decl.banco,
        "importe_pagado": _num(decl.importe_pagado),
        "rectificatoria": decl.rectificatoria,
        "medio": decl.medio_presentacion,
        "a_tiempo": a_tiempo,
        "casillas": decl.casillas,
    }


def _tributos(decl: DeclaracionPresentada) -> list[dict[str, Any]]:
    return [
        {**t, "importe": _num(t["importe"]), "clase_label": CLASES[t["clase"]]}
        for t in tributos_de(decl.constancia)
    ]


def _boleta(decl: DeclaracionPresentada) -> dict[str, Any]:
    c = decl.constancia or {}
    return {
        "tributos": _tributos(decl),
        "forma_pago": c.get("formaPago") or "",
        "tipo_declaracion": c.get("tipoDeclaracion") or "",
        "nro_operacion": (c.get("numeroOperacion") or decl.nro_operacion_banco or "").lstrip("0"),
        "nro_orden": decl.nro_orden,
        "fecha": decl.fecha_presentacion,
        "banco": decl.banco,
        "importe": _num(decl.importe_pagado),
        "paga_orden": decl.nro_orden_original,
        "descripcion": decl.descripcion,
        "tipo": decl.tipo_formulario_desc or decl.tipo_formulario,
        "detalle": decl.casillas,
    }


def resumen(account_ruc: str, *, desde: str | None = None, hoy: date | None = None) -> dict[str, Any]:
    hoy = hoy or timezone.localdate()
    filas = DeclaracionPresentada.objects.de(account_ruc)
    if desde:
        filas = filas.filter(periodo__gte=desde)
    por_periodo: dict[str, list[DeclaracionPresentada]] = defaultdict(list)
    for fila in filas.order_by("periodo", "fecha_presentacion", "nro_orden"):
        por_periodo[fila.periodo].append(fila)

    periodos: list[dict[str, Any]] = []
    for periodo in sorted(por_periodo, reverse=True):
        grupo = por_periodo[periodo]
        vencimiento = vencimiento_de(account_ruc, periodo) if not periodo.endswith("13") else None
        igv = [d for d in grupo if d.formulario == Formulario.IGV_RENTA]
        plame = [d for d in grupo if d.formulario == Formulario.PLAME]
        boletas = [d for d in grupo if d.formulario == Formulario.BOLETA]
        detracciones = [d for d in grupo if d.formulario == Formulario.DETRACCION]
        otros = [d for d in grupo if d.formulario not in (
            Formulario.IGV_RENTA, Formulario.PLAME, Formulario.BOLETA, Formulario.DETRACCION,
        )]
        decl_621 = igv[-1] if igv else None
        declarado = resumen_621(decl_621.casillas) if decl_621 else None
        total_pagado = sum((d.importe_pagado for d in grupo), Decimal(0))
        periodos.append({
            "periodo": periodo,
            "label": etiqueta(periodo),
            "vencimiento": vencimiento,
            "igv_renta": _declaracion(decl_621, vencimiento) if decl_621 else None,
            "igv_renta_declarado": {k: _num(v) for k, v in declarado.items()} if declarado else None,
            "rectificatorias_621": max(0, len(igv) - 1),
            "plame": _declaracion(plame[-1], vencimiento) if plame else None,
            "boletas": [_boleta(d) for d in boletas],
            "detracciones": [_boleta(d) for d in detracciones],
            "otros": [
                {"formulario": d.formulario, "descripcion": d.descripcion, **_declaracion(d, None)}
                for d in otros
            ],
            "total_pagado": _num(total_pagado),
        })

    ultima = ConsultaDeclaraciones.objects.filter(account_ruc=account_ruc).first()
    doce = [p for p in periodos if not p["periodo"].endswith("13")][:12]
    return {
        "ultima_consulta": {
            "fecha": ultima.created_at,
            "ok": ultima.succeeded,
            "error": ultima.error,
            "desde": ultima.periodo_desde,
            "hasta": ultima.periodo_hasta,
        } if ultima else None,
        "periodo_que_toca": ultimo_periodo_cerrado(hoy),
        "totales": {
            "pagado_12m": _num(sum((Decimal(str(p["total_pagado"] or 0)) for p in doce), Decimal(0))),
            "periodos_con_621_12m": sum(1 for p in doce if p["igv_renta"]),
            "periodos_12m": len(doce),
        },
        "periodos": periodos,
    }
