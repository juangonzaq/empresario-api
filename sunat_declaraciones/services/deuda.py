"""«¿Debo algo a SUNAT y qué he pagado?», con lo que ya se captura.

Hoy no se lee el saldo de deuda de SOL (valores pendientes con intereses);
esta vista cruza lo que sí hay:

* **Pagos de deuda**: el Formulario 1662 (boleta de pago) es cómo se paga un
  valor, una multa o los intereses; la consulta de declaraciones lo trae con
  fecha e importe.
* **Valores notificados**: cada deuda nace en el buzón SOL como Orden de
  Pago, Resolución de Multa o de Determinación; si no se paga, llega la
  Resolución de Ejecución Coactiva, y cuando se salda, la de Conclusión.
* **Ficha RUC**: si SUNAT publica deuda coactiva del RUC.

Son piezas del mismo cuento y se enseñan juntas; el saldo exacto sigue
siendo el de SOL.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import Q
from django.utils import timezone

from ..models import DeclaracionPresentada
from .plazos import periodo_anterior
from .tributos import CLASES, tributos_de

MESES = 12

# Asuntos del buzón que abren, agravan o cierran una deuda.
VALOR = re.compile(r"orden de pago|resoluci[oó]n de multa|resoluci[oó]n de determinaci[oó]n", re.I)
MULTA = re.compile(r"resoluci[oó]n de multa", re.I)
COACTIVA = re.compile(r"ejecuci[oó]n coactiva", re.I)
CONCLUSION = re.compile(r"conclusi[oó]n", re.I)


def _num(v: Decimal | float | None) -> float | None:
    return None if v is None else float(v)


def _periodos(hoy: date) -> list[str]:
    actual = f"{hoy.year}{hoy.month:02d}"
    doce = [actual]
    for _ in range(MESES - 1):
        doce.append(periodo_anterior(doce[-1]))
    return list(reversed(doce))


def _pagos(account_ruc: str, hoy: date) -> dict[str, Any]:
    periodos = _periodos(hoy)
    desde = date(hoy.year - 1, hoy.month, 1)
    boletas = list(
        DeclaracionPresentada.objects.de(account_ruc)
        .filter(formulario="1662", fecha_presentacion__gte=desde)
        .order_by("fecha_presentacion")
    )
    # La serie va por mes de pago, no por periodo tributario: una boleta de
    # agosto puede saldar abril, y lo que interesa aquí es cuándo salió la plata.
    por_mes: dict[str, Decimal] = {p: Decimal(0) for p in periodos}
    # Desglose por clase de tributo de cada mes, para el tooltip del card.
    clases_mes: dict[str, dict[str, Decimal]] = {p: {} for p in periodos}
    boletas_mes: dict[str, int] = {p: 0 for p in periodos}
    for b in boletas:
        if b.fecha_presentacion:
            clave = f"{b.fecha_presentacion.year}{b.fecha_presentacion.month:02d}"
            if clave in por_mes:
                por_mes[clave] += b.importe_pagado
                boletas_mes[clave] += 1
                for t in tributos_de(b.constancia):
                    if t["importe"] is not None:
                        clases_mes[clave][t["clase"]] = clases_mes[clave].get(t["clase"], Decimal(0)) + t["importe"]
    # Qué se pagó, por clase de tributo (la constancia de cada boleta lo
    # dice), y las multas una a una: son lo que la persona quiere ver.
    por_clase: dict[str, dict[str, Any]] = {}
    multas: list[dict[str, Any]] = []
    for b in boletas:
        for t in tributos_de(b.constancia):
            if t["importe"] is None:
                continue
            fila = por_clase.setdefault(t["clase"], {"clase": t["clase"], "label": CLASES[t["clase"]], "importe": Decimal(0), "pagos": 0})
            fila["importe"] += t["importe"]
            fila["pagos"] += 1
            if t["clase"] == "multa":
                multas.append({
                    "fecha": b.fecha_presentacion,
                    "codigo": t["codigo"],
                    "descripcion": t["descripcion"],
                    "periodo": t["periodo"] or b.periodo,
                    "importe": _num(t["importe"]),
                    "nro_orden": b.nro_orden,
                })
    multas.sort(key=lambda m: (m["fecha"] or date.min), reverse=True)
    ultimo = boletas[-1] if boletas else None
    return {
        "total_12m": _num(sum(por_mes.values(), Decimal(0))),
        "boletas_12m": len(boletas),
        "serie": [
            {
                "periodo": p,
                "importe": _num(v),
                "boletas": boletas_mes[p],
                "clases": [
                    {"clase": c, "label": CLASES[c], "importe": _num(i)}
                    for c, i in sorted(clases_mes[p].items(), key=lambda kv: kv[1], reverse=True)
                ],
            }
            for p, v in por_mes.items()
        ],
        "por_clase": sorted(
            ({**f, "importe": _num(f["importe"])} for f in por_clase.values()),
            key=lambda f: f["importe"] or 0, reverse=True,
        ),
        "multas": {
            "total_12m": _num(sum((Decimal(str(m["importe"])) for m in multas), Decimal(0))),
            "pagos_12m": len(multas),
            "detalle": multas[:12],
        },
        "ultimo": None if ultimo is None else {
            "fecha": ultimo.fecha_presentacion,
            "importe": _num(ultimo.importe_pagado),
            "periodo": ultimo.periodo,
        },
    }


def _valores(account_ruc: str, hoy: date) -> dict[str, Any]:
    from sunat_mailbox.models import Message

    desde = date(hoy.year - 1, hoy.month, 1)
    mensajes = (
        Message.objects.for_taxpayer(account_ruc)
        .filter(published_at__date__gte=desde)
        .filter(
            Q(subject__iregex=VALOR.pattern) | Q(subject__iregex=COACTIVA.pattern)
            | Q(subject__iregex=r"resoluci[oó]n coactiva")
        )
        .select_related("analysis")
        .order_by("-published_at")
    )
    notificados = coactiva = concluidos = multas = 0
    importe = Decimal(0)
    con_importe = 0
    ultimo: dict[str, Any] | None = None
    for m in mensajes:
        asunto = m.subject or ""
        if CONCLUSION.search(asunto):
            concluidos += 1
            continue
        if COACTIVA.search(asunto):
            coactiva += 1
        elif VALOR.search(asunto):
            notificados += 1
            if MULTA.search(asunto):
                multas += 1
        else:
            continue
        analisis = getattr(m, "analysis", None)
        if analisis is not None and analisis.amount is not None:
            importe += analisis.amount
            con_importe += 1
        if ultimo is None:
            ultimo = {
                "fecha": m.published_at.date() if m.published_at else None,
                "asunto": re.sub(r"^ASUNTO:\s*", "", asunto)[:120],
                "en_coactiva": bool(COACTIVA.search(asunto)),
            }
    return {
        "notificados_12m": notificados,
        "multas_12m": multas,
        "coactiva_12m": coactiva,
        "concluidos_12m": concluidos,
        # Suma de lo que la lectura del buzón pudo extraer; parcial hasta que
        # todos los mensajes estén analizados.
        "importe_notificado": _num(importe),
        "con_importe": con_importe,
        "ultimo": ultimo,
    }


def _ficha(account_ruc: str) -> dict[str, Any]:
    try:
        from ruc_profile.models import RucSnapshot
    except Exception:  # pragma: no cover
        return {"coactiva_publicada": None, "al": None}
    ficha = RucSnapshot.objects.filter(ruc=account_ruc, succeeded=True).order_by("-captured_on").first()
    return {
        "coactiva_publicada": None if ficha is None else bool(ficha.has_coactive_debt),
        "al": ficha.captured_on if ficha else None,
    }


def resumen_deuda(account_ruc: str, hoy: date | None = None) -> dict[str, Any]:
    hoy = hoy or timezone.localdate()
    return {
        "hoy": hoy,
        "pagos": _pagos(account_ruc, hoy),
        "valores": _valores(account_ruc, hoy),
        "ficha": _ficha(account_ruc),
    }
