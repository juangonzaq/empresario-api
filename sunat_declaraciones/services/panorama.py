"""Lecturas transversales de lo declarado, para los módulos que no son
Finanzas: el Inicio, el calendario, Colaboradores, el Balance y el asistente.

Todo sale de ``DeclaracionPresentada`` y ``DeclaracionAnual``; aquí no se
consulta a SUNAT ni se recalcula nada, solo se arma la vista que cada
pantalla necesita."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.utils import timezone

from ..models import DeclaracionAnual, DeclaracionPresentada, Formulario
from .casillas import numero, resumen_621
from .plazos import periodo_anterior, ultimo_periodo_cerrado, vencimiento_de
from .renta_anual import con_nombre, vigentes as anuales_vigentes
from .sync import vigentes_621
from .tributos import pagado_por_clase


def _num(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def vigentes_plame(account_ruc: str) -> dict[str, DeclaracionPresentada]:
    out: dict[str, DeclaracionPresentada] = {}
    for d in (
        DeclaracionPresentada.objects.de(account_ruc).formulario(Formulario.PLAME)
        .exclude(periodo__endswith="13").order_by("periodo", "fecha_presentacion", "nro_orden")
    ):
        out[d.periodo] = d
    return out


def presentaciones_por_periodo(account_ruc: str) -> dict[str, dict[str, Any]]:
    """Periodo → {"621": {...}, "0601": {...}} con fecha y nro. de orden.
    Es lo que el calendario cruza con sus vencimientos."""
    out: dict[str, dict[str, Any]] = {}
    for clave, vig in (("621", vigentes_621(account_ruc)), ("0601", vigentes_plame(account_ruc))):
        for periodo, d in vig.items():
            out.setdefault(periodo, {})[clave] = {
                "fecha": d.fecha_presentacion, "nro_orden": d.nro_orden, "rectificatoria": d.rectificatoria,
            }
    return out


# ------------------------------------------------------------------ Inicio

def estado_del_mes(account_ruc: str, hoy: date | None = None) -> dict[str, Any] | None:
    """El card del Inicio: ¿está presentado lo del periodo que toca?"""
    hoy = hoy or timezone.localdate()
    if not DeclaracionPresentada.objects.de(account_ruc).exists():
        return None
    periodo = ultimo_periodo_cerrado(hoy)
    vencimiento = vencimiento_de(account_ruc, periodo)
    pres = presentaciones_por_periodo(account_ruc)
    actual = pres.get(periodo, {})
    anterior_p = periodo_anterior(periodo)
    anterior = pres.get(anterior_p, {})

    def _f(clave: str, fuente: dict) -> dict[str, Any]:
        d = fuente.get(clave)
        return {
            "presentado": d is not None,
            "fecha": d["fecha"] if d else None,
            "a_tiempo": (d["fecha"] <= vencimiento) if (d and vencimiento and d["fecha"]) else None,
        }

    doce = [periodo]
    for _ in range(11):
        doce.append(periodo_anterior(doce[-1]))
    pagado_12m = sum(
        (d.importe_pagado for d in DeclaracionPresentada.objects.de(account_ruc).filter(periodo__in=doce)),
        Decimal(0),
    )
    tiene_plame = bool(vigentes_plame(account_ruc))
    ultima = DeclaracionPresentada.objects.de(account_ruc).order_by("-visto_el").values_list("visto_el", flat=True).first()
    return {
        "periodo": periodo,
        "vencimiento": vencimiento,
        "vencido": bool(vencimiento and vencimiento < hoy),
        "dias_para_vencer": (vencimiento - hoy).days if vencimiento else None,
        "igv_renta": _f("621", actual),
        "plame": _f("0601", actual) if tiene_plame else None,
        "anterior": {"periodo": anterior_p, "igv_renta": _f("621", anterior)},
        "pagado_12m": _num(pagado_12m),
        "ultima_sync": ultima,
    }


# ------------------------------------------------------------ Colaboradores

def planilla_vs_plame(account_ruc: str, meses: int = 12) -> dict[str, Any]:
    """Lo declarado en la PLAME frente a lo que la plataforma sabe de la
    planilla: colaboradores activos y, si existe, la planilla propia cerrada."""
    from colaboradores.models import Colaborador
    from payroll.models import PayrollEntry, PayrollPeriod, PayrollStatus

    activos = Colaborador.objects.filter(taxpayer_id=account_ruc, is_active=True).count()
    propios: dict[str, dict[str, Any]] = {}
    for p in PayrollPeriod.objects.filter(taxpayer_id=account_ruc, status=PayrollStatus.CLOSED):
        entradas = PayrollEntry.objects.filter(period=p)
        propios[f"{p.year}{p.month:02d}"] = {
            "trabajadores": entradas.count(),
            "costo": sum((e.total_employer_cost for e in entradas), Decimal(0)),
        }
    filas = []
    for periodo, d in sorted(vigentes_plame(account_ruc).items(), reverse=True)[:meses]:
        c = d.constancia or {}
        remuneraciones = numero(d.casillas, "C452")
        essalud = numero(d.casillas, "C412")
        onp = numero(d.casillas, "C411")
        pagado = pagado_por_clase(c)
        trabajadores = c.get("trabajadores")
        try:
            trabajadores = int(str(trabajadores)) if trabajadores not in (None, "") else None
        except ValueError:
            trabajadores = None
        propio = propios.get(periodo)
        filas.append({
            "periodo": periodo,
            "nro_orden": d.nro_orden,
            "fecha_presentacion": d.fecha_presentacion,
            "trabajadores": trabajadores,
            "prestadores_4ta": int(c.get("personalCuarta") or 0) if str(c.get("personalCuarta") or "").isdigit() else None,
            "remuneraciones": _num(remuneraciones),
            "essalud": _num(essalud),
            "onp": _num(onp),
            "costo_declarado": _num((remuneraciones or Decimal(0)) + (essalud or Decimal(0))) if remuneraciones is not None else None,
            "pagado_con_plame": _num(d.importe_pagado),
            "planilla_propia": None if propio is None else {
                "trabajadores": propio["trabajadores"], "costo": _num(propio["costo"]),
            },
            "diferencia_trabajadores": None if (trabajadores is None) else trabajadores - (propio["trabajadores"] if propio else activos),
        })
    return {
        "colaboradores_activos": activos,
        "periodos": filas,
    }


# ---------------------------------------------------------------- Balance

BALANCE_MAPEO: list[tuple[str, list[tuple[str, int]]]] = [
    ("CASH_LINE", [("efectivo", 1)]),
    ("TRADE_RECEIVABLES_LINE", [("cuentas_por_cobrar_comerciales", 1)]),
    ("OTHER_RECEIVABLES_LINE", [("cuentas_por_cobrar_relacionadas", 1)]),
    ("INVENTORY_LINE", [("mercaderias", 1)]),
    ("PPE_LINE", [("propiedad_planta_equipo", 1), ("depreciacion_acumulada", -1)]),
    ("TOTAL_ASSETS", [("total_activo", 1)]),
    ("TRADE_PAYABLES_LINE", [("cuentas_por_pagar_comerciales", 1)]),
    ("OTHER_PAYABLES_LINE", [("tributos_por_pagar", 1), ("remuneraciones_por_pagar", 1), ("provisiones", 1)]),
    ("FIN_OBLIG_CURRENT_LINE", [("obligaciones_financieras", 1)]),
    ("TOTAL_LIABILITIES", [("total_pasivo", 1)]),
    ("SHARE_CAPITAL_LINE", [("capital", 1)]),
    ("RETAINED_EARNINGS_LINE", [("resultados_acumulados", 1), ("resultados_acumulados_negativos", -1)]),
    ("PERIOD_RESULT", [("utilidad_del_ejercicio", 1), ("perdida_del_ejercicio", -1)]),
]


def cruce_balance(account_ruc: str, year: int) -> dict[str, Any]:
    """Balance de Finanzas al 31/12 frente al declarado en el 710."""
    from financials.services.statements import balance_sheet

    decl = anuales_vigentes(account_ruc).get(str(year))
    balance = balance_sheet(account_ruc, year, 12)
    por_codigo = {l["code"]: l for l in balance["lines"]}
    casillas = con_nombre(decl.casillas) if decl else {}
    filas = []
    for codigo, partes in BALANCE_MAPEO:
        linea = por_codigo.get(codigo)
        if linea is None:
            continue
        finanzas = Decimal(str(linea["amount"]))
        declarado = None
        if decl:
            valores = [(casillas.get(n), s) for n, s in partes]
            if any(v is not None for v, _ in valores):
                declarado = sum((v * s for v, s in valores if v is not None), Decimal(0))
        diferencia = None if declarado is None else declarado - finanzas
        filas.append({
            "code": codigo, "name": linea["name"], "line_type": linea["line_type"], "section": linea["section"],
            "finanzas": float(finanzas),
            "declarado": None if declarado is None else float(declarado),
            "diferencia": None if diferencia is None else float(diferencia),
            "diferencia_pct": None if diferencia is None or not finanzas else round(float(diferencia / abs(finanzas) * 100), 1),
            "nota": "",
        })
    return {"year": year, "declaracion": None if decl is None else {"nro_orden": decl.nro_orden}, "rows": filas}


# --------------------------------------------------------------- Asistente

def resumen_para_asistente(account_ruc: str, hoy: date | None = None) -> dict[str, Any]:
    """Lo que un asesor querría tener a mano antes de responder."""
    hoy = hoy or timezone.localdate()
    estado = estado_del_mes(account_ruc, hoy)
    vig = vigentes_621(account_ruc)
    meses = []
    for periodo in sorted(vig, reverse=True)[:6]:
        r = resumen_621(vig[periodo].casillas)
        meses.append({
            "periodo": periodo, "ventas": _num(r["ventas_base"]), "compras": _num(r["compras_base"]),
            "igv_resultante": _num(r["igv_a_pagar"]), "pago_a_cuenta": _num(r["renta_pago_a_cuenta"]),
            "presentado": vig[periodo].fecha_presentacion,
        })
    anio = str(hoy.year)
    clases: dict[str, Decimal] = {}
    for b in DeclaracionPresentada.objects.de(account_ruc).formulario(Formulario.BOLETA).filter(periodo__startswith=anio):
        for k, v in pagado_por_clase(b.constancia).items():
            clases[k] = clases.get(k, Decimal(0)) + v
    anuales = []
    for ejercicio, d in sorted(anuales_vigentes(account_ruc).items(), reverse=True)[:3]:
        c = con_nombre(d.casillas)
        anuales.append({
            "ejercicio": ejercicio, "ventas_netas": _num(c["ventas_netas"]),
            "resultado_neto": _num((c["utilidad_neta"] or Decimal(0)) - (c["perdida_neta"] or Decimal(0))),
            "impuesto": _num(c["impuesto_a_la_renta"]), "saldo_a_favor": _num(c["saldo_a_favor"]),
            "perdida_arrastrable": _num(c["saldo_perdidas_no_compensadas"]),
            "coeficiente_pago_a_cuenta": _num(c["coeficiente_pago_a_cuenta"]),
        })
    return {
        "estado_del_mes": estado,
        "ultimos_621": meses,
        "pagado_por_tributo_en_el_anio": {k: float(v) for k, v in clases.items()},
        "dj_anuales": anuales,
    }


def lineas_para_asistente(account_ruc: str) -> list[str]:
    r = resumen_para_asistente(account_ruc)
    if not r["estado_del_mes"] and not r["dj_anuales"]:
        return []
    out: list[str] = []
    e = r["estado_del_mes"]
    if e:
        def _txt(f):
            if not f:
                return "no aplica"
            if not f["presentado"]:
                return "NO presentado"
            return f"presentado el {f['fecha']:%d/%m/%Y}" + (" (fuera de plazo)" if f["a_tiempo"] is False else "")
        out.append(
            f"[declaraciones] Periodo que toca {e['periodo']}: 621 {_txt(e['igv_renta'])}; PLAME {_txt(e['plame'])}"
            + (f"; vence el {e['vencimiento']:%d/%m/%Y}" if e["vencimiento"] else "")
            + f". Pagado a SUNAT en 12 meses: S/ {e['pagado_12m']:,.0f}."
        )
    for m in r["ultimos_621"]:
        out.append(
            f"[621 {m['periodo']}] ventas {m['ventas']:,.0f} · compras {m['compras']:,.0f} · IGV resultante {m['igv_resultante']:,.0f} · pago a cuenta {m['pago_a_cuenta']:,.0f}"
            if None not in (m['ventas'], m['compras'], m['igv_resultante'], m['pago_a_cuenta']) else f"[621 {m['periodo']}] presentado"
        )
    if r["pagado_por_tributo_en_el_anio"]:
        out.append("[pagos del año por tributo] " + " · ".join(f"{k}: S/ {v:,.0f}" for k, v in r["pagado_por_tributo_en_el_anio"].items()))
    for a in r["dj_anuales"]:
        out.append(
            f"[DJ anual {a['ejercicio']}] ventas {a['ventas_netas'] or 0:,.0f} · resultado neto {a['resultado_neto'] or 0:,.0f} · impuesto {a['impuesto'] or 0:,.0f}"
            + (f" · saldo a favor {a['saldo_a_favor']:,.0f}" if a["saldo_a_favor"] else "")
            + (f" · pérdida arrastrable {a['perdida_arrastrable']:,.0f}" if a["perdida_arrastrable"] else "")
            + (f" · coeficiente pago a cuenta {a['coeficiente_pago_a_cuenta']}" if a["coeficiente_pago_a_cuenta"] is not None else "")
        )
    return out
