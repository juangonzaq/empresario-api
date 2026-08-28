"""Hallazgos que salen de cruzar lo declarado con lo pagado y con el
cronograma. Los consume ``finance_analytics.services.alerts`` para volcarlos
en ``FinanceAlert`` con el resto de alertas del módulo.

Se dice lo que se ve, no lo que se supone: un 621 en S/ 0 puede ser saldo a
favor, compensación o deuda pendiente; solo se alerta cuando el propio
formulario dice que había importe a pagar y no aparece pago que lo cubra.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.utils import timezone

from ..models import ConsultaDeclaraciones, DeclaracionPresentada, Formulario
from .casillas import resumen_621
from .plazos import periodo_anterior, ultimo_periodo_cerrado, vencimiento_de
from .sync import vigentes_621
from .tributos import pagado_por_clase

# Cuántos periodos cerrados hacia atrás se miran para omisiones y pagos. Más
# lejos ya no es una alerta operativa sino historia: una deuda de hace dos
# años se ve en «Valores» de SOL, no aquí.
PERIODOS_OMISION = 6
PERIODOS_PAGO = 12


def _etiqueta(periodo: str) -> str:
    return f"{periodo[4:6]}/{periodo[:4]}"


def hallazgos(account_ruc: str, hoy: date | None = None) -> list[dict[str, Any]]:
    hoy = hoy or timezone.localdate()
    if not DeclaracionPresentada.objects.de(account_ruc).exists():
        return []
    vigentes = vigentes_621(account_ruc)
    salida: list[dict[str, Any]] = []

    # 1) Periodos cerrados sin 621, dentro de la ventana consultada: solo se
    # puede afirmar «no aparece» de un periodo que de verdad se le preguntó a
    # SUNAT. Fuera de lo consultado no hay dato, no omisión.
    consultado_hasta = (
        ConsultaDeclaraciones.objects.filter(account_ruc=account_ruc, succeeded=True)
        .order_by("-periodo_hasta").values_list("periodo_hasta", flat=True).first()
    )
    primero = min(vigentes) if vigentes else None
    periodo = ultimo_periodo_cerrado(hoy)
    for _ in range(PERIODOS_OMISION):
        if primero and periodo < primero:
            break
        if consultado_hasta and periodo > consultado_hasta:
            periodo = periodo_anterior(periodo)
            continue
        vencimiento = vencimiento_de(account_ruc, periodo)
        vencido = vencimiento is None or vencimiento < hoy
        if periodo not in vigentes and vencido:
            salida.append({
                "dedup_key": f"decl:omitida:{periodo}",
                "alert_type": "declaracion_omitida",
                "severity": "high",
                "period": periodo,
                "title": f"No aparece el F.V. 621 del periodo {_etiqueta(periodo)}",
                "explanation": (
                    f"En la consulta de declaraciones de SUNAT no figura el 621 de {_etiqueta(periodo)}"
                    + (f", que venció el {vencimiento:%d/%m/%Y}." if vencimiento else ".")
                    + " Puede estar pendiente o haberse presentado después de la última sincronización."
                ),
                "recommendation": "Verifica en SOL si está presentado; si no, preséntalo cuanto antes: la multa por omisión se reduce si se regulariza voluntariamente.",
                "source": "SUNAT · Consulta de Declaraciones y Pagos",
            })
        periodo = periodo_anterior(periodo)

    # Lo pagado por periodo que cubre el 621: con constancia se cuentan solo
    # IGV y renta (una boleta de ONP no paga el IGV); sin ella, toda la boleta.
    boletas_por_periodo: dict[str, Decimal] = {}
    for b in DeclaracionPresentada.objects.de(account_ruc).formulario(Formulario.BOLETA):
        if b.constancia:
            clases = pagado_por_clase(b.constancia)
            monto = clases.get("igv", Decimal(0)) + clases.get("renta", Decimal(0))
        else:
            monto = b.importe_pagado
        boletas_por_periodo[b.periodo] = boletas_por_periodo.get(b.periodo, Decimal(0)) + monto

    limite_pago = ultimo_periodo_cerrado(hoy)
    for _ in range(PERIODOS_PAGO - 1):
        limite_pago = periodo_anterior(limite_pago)

    for periodo, decl in vigentes.items():
        if periodo < limite_pago:
            continue
        # 2) Presentado fuera de plazo (solo cuando hay cronograma cargado).
        vencimiento = vencimiento_de(account_ruc, periodo)
        if vencimiento and decl.fecha_presentacion and decl.fecha_presentacion > vencimiento:
            dias = (decl.fecha_presentacion - vencimiento).days
            salida.append({
                "dedup_key": f"decl:tarde:{periodo}",
                "alert_type": "declaracion_fuera_de_plazo",
                "severity": "medium",
                "period": periodo,
                "title": f"El 621 de {_etiqueta(periodo)} se presentó {dias} día(s) después del vencimiento",
                "explanation": (
                    f"Vencía el {vencimiento:%d/%m/%Y} y SUNAT lo registra presentado el "
                    f"{decl.fecha_presentacion:%d/%m/%Y} (orden {decl.nro_orden})."
                ),
                "recommendation": "Revisa si SUNAT emitió multa por presentación extemporánea y los intereses del tributo pagado tarde.",
                "source": "SUNAT · Consulta de Declaraciones y Pagos",
            })

        # 3) Declarado con importe a pagar, sin pago que lo cubra.
        r = resumen_621(decl.casillas)
        a_pagar = r["total_a_pagar"] or Decimal(0)
        pagado = decl.importe_pagado + boletas_por_periodo.get(periodo, Decimal(0))
        if a_pagar > 0 and pagado < a_pagar:
            faltante = a_pagar - pagado
            salida.append({
                "dedup_key": f"decl:sinpago:{periodo}",
                "alert_type": "declaracion_sin_pago",
                "severity": "high",
                "period": periodo,
                "title": f"El 621 de {_etiqueta(periodo)} declara S/ {a_pagar:,.0f} a pagar y solo se ven S/ {pagado:,.0f} pagados",
                "explanation": (
                    f"El formulario (orden {decl.nro_orden}) determina IGV y pago a cuenta por S/ {a_pagar:,.2f}; "
                    f"entre la declaración y las boletas 1662 de IGV/renta del periodo SUNAT registra S/ {pagado:,.2f}. "
                    "Puede haberse pagado con saldo a favor, compensación o fraccionamiento, que aquí no se ven."
                ),
                "amount": faltante,
                "currency": "PEN",
                "recommendation": "Confirma en SOL (Valores / deuda pendiente) si quedó deuda por ese periodo; los intereses moratorios corren a diario.",
                "source": "SUNAT · Consulta de Declaraciones y Pagos",
            })
    return salida
