"""Guardar lo consultado y derivar de ello lo que otras apps ya esperan.

Tres destinos, en este orden:

1. ``DeclaracionPresentada``: la fila cruda, una por número de orden.
2. ``reconciliation.DeclaredSummary``: lo declarado en el 621 por periodo,
   con las casillas reales. Es lo que el motor de conciliación cruza con SIRE
   y lo que el módulo de obligaciones lee como «periodos declarados».
3. ``obligations.ObligationEvidence``: cada 621 presentado es evidencia
   *verificada* —viene de SUNAT— de la obligación mensual de ese periodo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from ..models import ConsultaDeclaraciones, DeclaracionPresentada, Formulario
from .casillas import resumen_621
from .client import ConsultaDeclaracionesClient, ventanas
from .plazos import fin_de_mes, periodo_anterior, periodo_siguiente, ultimo_periodo_cerrado

logger = logging.getLogger(__name__)

REGLA_MENSUAL = "tax-monthly-igv-renta"
REGLA_PLAME = "tax-monthly-plame"
# Cuántos periodos hacia atrás trae la primera carga. SUNAT publica desde
# 2018, pero tres años bastan para lo que Finanzas compara.
PERIODOS_INICIALES = 36
# Formularios cuya constancia se pide: boletas y detracciones (para saber qué
# tributo pagan) y las declaraciones (forma de pago y, en la PLAME, plantilla).
CON_CONSTANCIA = {"1662", "detr", "0621", "0601"}
# Lo que se repasa en una sincronización corriente: el periodo que toca y el
# anterior, por si se presentó tarde o hubo rectificatoria.
PERIODOS_RECIENTES = 3


@dataclass
class ResultadoDeclaraciones:
    filas: int = 0
    nuevas: int = 0
    actualizadas: int = 0
    periodos_declarados: int = 0
    evidencias: int = 0
    transacciones: int = 0


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def _fecha(ms: Any) -> datetime | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _fecha_str(texto: str) -> date | None:
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date()
    except ValueError:
        return None


def normalizar(fila: dict[str, Any]) -> dict[str, Any]:
    """De la fila de SUNAT a los campos del modelo."""
    tipo = _texto(fila.get("tipoForm"))
    mapa_tipos = fila.get("mapTipoForm") or {}
    presentado = _fecha(fila.get("fecPres"))
    fecha_presentacion = presentado.date() if presentado else _fecha_str(_texto(fila.get("strFecPres")))
    nro_orden_original = _texto(fila.get("numOrdOri"))
    return {
        "periodo": _texto(fila.get("perTri")),
        "formulario": _texto(fila.get("codFor")),
        "descripcion": _texto(fila.get("desFor"))[:120],
        "nro_orden": _texto(fila.get("numOrd")),
        "fecha_presentacion": fecha_presentacion,
        "fecha_pago": _fecha(fila.get("fecPago")),
        "banco": _texto(fila.get("nomEntFin"))[:120],
        "importe_pagado": Decimal(str(fila.get("mtoPag") or 0)),
        "tipo_formulario": tipo,
        "tipo_formulario_desc": _texto(mapa_tipos.get(tipo))[:80] if isinstance(mapa_tipos, dict) else "",
        "medio_presentacion": _texto(fila.get("medioPres")),
        "nro_orden_original": "" if nro_orden_original in ("", "0") else nro_orden_original,
        "nro_operacion_sunat": _texto(fila.get("numeroOperacionSunat"))[:30],
        "nro_operacion_banco": _texto(fila.get("numOpebco"))[:40],
        "es_boleta": _texto(fila.get("indBoleta")) == "1" or _texto(fila.get("codFor")) == Formulario.BOLETA,
        "rectificatoria": _texto(fila.get("descTipoDecla")) not in ("", "0"),
        "casillas": fila.get("casillas") or {},
        "raw": {k: v for k, v in fila.items() if k not in ("casillas", "mapTipoForm", "mapMedioPres")},
    }


@transaction.atomic
def guardar(
    account_ruc: str, filas: list[dict[str, Any]], visto_el: date | None = None,
    constancias: dict[str, dict[str, Any]] | None = None,
) -> ResultadoDeclaraciones:
    visto_el = visto_el or timezone.localdate()
    constancias = constancias or {}
    resultado = ResultadoDeclaraciones(filas=len(filas))
    campos = [
        "periodo", "formulario", "descripcion", "fecha_presentacion", "fecha_pago", "banco",
        "importe_pagado", "tipo_formulario", "tipo_formulario_desc", "medio_presentacion",
        "nro_orden_original", "nro_operacion_sunat", "nro_operacion_banco", "es_boleta",
        "rectificatoria", "casillas", "raw",
    ]
    for fila in filas:
        datos = normalizar(fila)
        if not datos["nro_orden"] or len(datos["periodo"]) != 6:
            continue
        if datos["nro_orden"] in constancias:
            datos["constancia"] = constancias[datos["nro_orden"]]
        actual = DeclaracionPresentada.objects.filter(
            account_ruc=account_ruc, nro_orden=datos["nro_orden"],
        ).first()
        if actual is None:
            DeclaracionPresentada.objects.create(account_ruc=account_ruc, visto_el=visto_el, **datos)
            resultado.nuevas += 1
            continue
        cambios = [c for c in [*campos, "constancia"] if c in datos and getattr(actual, c) != datos[c]]
        for c in cambios:
            setattr(actual, c, datos[c])
        actual.visto_el = visto_el
        actual.save(update_fields=[*cambios, "visto_el", "updated_at"])
        if cambios:
            resultado.actualizadas += 1
    return resultado


def vigentes_621(account_ruc: str) -> dict[str, DeclaracionPresentada]:
    """La última presentación del 621 de cada periodo mensual (la rectificatoria
    más reciente manda). El AAAA13 anual se deja fuera: no es un 621."""
    vigentes: dict[str, DeclaracionPresentada] = {}
    filas = (
        DeclaracionPresentada.objects.de(account_ruc).formulario(Formulario.IGV_RENTA)
        .exclude(periodo__endswith="13")
        .order_by("periodo", "fecha_presentacion", "nro_orden")
    )
    for fila in filas:
        vigentes[fila.periodo] = fila
    return vigentes


def alimentar_declarado(account_ruc: str) -> int:
    """Escribe ``DeclaredSummary`` desde las casillas del 621 vigente."""
    from reconciliation.models import DeclaredSummary

    cuantos = 0
    for periodo, decl in vigentes_621(account_ruc).items():
        r = resumen_621(decl.casillas)
        DeclaredSummary.objects.update_or_create(
            account_ruc=account_ruc, period=periodo,
            defaults={
                "sales_base": r["ventas_base"],
                "sales_igv": r["ventas_igv"],
                "purchases_base": r["compras_base"],
                "purchases_igv": r["compras_igv"],
                "igv_payable": r["igv_a_pagar"],
                "income_tax_declared": r["renta_pago_a_cuenta"],
                "total_declared": r["total_a_pagar"],
                "filed_at": decl.fecha_presentacion,
                "source": DeclaredSummary.Source.IMPORT,
                "raw": {
                    "origen": "sunat_declaraciones",
                    "nro_orden": decl.nro_orden,
                    "rectificatoria": decl.rectificatoria,
                    "importe_pagado": str(decl.importe_pagado),
                },
            },
        )
        cuantos += 1
    return cuantos


def registrar_evidencia(account_ruc: str) -> int:
    """Cada 621 vigente es evidencia verificada de la obligación mensual.

    La evidencia vale hasta fin del mes siguiente al periodo: en ese mes la
    obligación «que toca» pasa a ser el periodo siguiente y esta ya no la
    cubre. Así la pantalla dice «verificada» solo mientras es verdad.
    """
    from obligations import enums
    from obligations.models import CompanyObligation, ObligationEvidence

    from .panorama import vigentes_plame

    cuantos = 0
    for regla, etiqueta, vigentes in (
        (REGLA_MENSUAL, "F.V. 621", vigentes_621(account_ruc)),
        (REGLA_PLAME, "PLAME (0601)", vigentes_plame(account_ruc)),
    ):
        obligacion = (
            CompanyObligation.objects.filter(account_ruc=account_ruc, rule__code=regla)
            .select_related("rule").first()
        )
        if obligacion is None:
            continue
        for periodo, decl in vigentes.items():
            referencia = {
                "model": "sunat_declaraciones.DeclaracionPresentada",
                "id": str(decl.pk),
                "period": periodo,
                "nro_orden": decl.nro_orden,
            }
            ObligationEvidence.objects.update_or_create(
                company_obligation=obligacion,
                reference__period=periodo,
                reference__model=referencia["model"],
                defaults={
                    "evidence_type": enums.EvidenceType.DECLARATION,
                    "verification_status": enums.VerificationStatus.VERIFIED,
                    "label": f"{etiqueta} · periodo {periodo[4:6]}/{periodo[:4]} · orden {decl.nro_orden}",
                    "reference": referencia,
                    "valid_from": decl.fecha_presentacion,
                    "valid_until": fin_de_mes(periodo_siguiente(periodo)),
                    "notes": "Presentación registrada en SUNAT (Consulta de Declaraciones y Pagos).",
                    "verified_at": timezone.now(),
                },
            )
            cuantos += 1
    return cuantos


def alimentar_resultados(account_ruc: str) -> int:
    """El pago a cuenta declarado pasa al Estado de Resultados como impuesto a
    la renta del mes (una transacción por periodo, fuente «Declaración SUNAT»)."""
    from financials.services.ingest import ingest_sunat_declarations

    r = ingest_sunat_declarations(account_ruc)
    return sum(v["created"] + v["updated"] for v in r.values())


def derivar(account_ruc: str, resultado: ResultadoDeclaraciones | None = None) -> ResultadoDeclaraciones:
    resultado = resultado or ResultadoDeclaraciones()
    resultado.periodos_declarados = alimentar_declarado(account_ruc)
    resultado.evidencias = registrar_evidencia(account_ruc)
    resultado.transacciones = alimentar_resultados(account_ruc)
    # El resumen del tablero se cachea por empresa; acaba de cambiar.
    try:
        from finance_analytics import cache as overview_cache

        overview_cache.invalidate(account_ruc)
    except Exception:  # noqa: BLE001 — la caché nunca debe frenar la sincronización
        logger.debug("No se pudo invalidar la caché del overview", exc_info=True)
    return resultado


def rango_por_defecto(inicial: bool, hoy: date | None = None) -> tuple[str, str]:
    hoy = hoy or timezone.localdate()
    hasta = f"{hoy.year}{hoy.month:02d}"
    desde = hasta
    for _ in range((PERIODOS_INICIALES if inicial else PERIODOS_RECIENTES) - 1):
        desde = periodo_anterior(desde)
    return desde, hasta


def sincronizar(
    account_ruc: str, username: str, password: str, *,
    desde: str | None = None, hasta: str | None = None, inicial: bool = False,
    headless: bool = True,
) -> ResultadoDeclaraciones:
    """Consulta SOL por ventanas, guarda y deriva. La bitácora se escribe
    también cuando falla, para que la pantalla pueda decir qué pasó.

    Las constancias se piden solo para las órdenes que aún no la tienen: es
    una llamada por boleta y no cambian una vez emitidas."""
    d, h = rango_por_defecto(inicial)
    desde, hasta = desde or d, hasta or h
    tramos = ventanas(desde, hasta)
    con_constancia = set(
        DeclaracionPresentada.objects.de(account_ruc).exclude(constancia={})
        .values_list("nro_orden", flat=True)
    )
    cliente = ConsultaDeclaracionesClient(account_ruc, username, password, headless=headless)
    try:
        filas, constancias = cliente.consultar(tramos, detallar=CON_CONSTANCIA, omitir=con_constancia)
    except Exception as exc:
        ConsultaDeclaraciones.objects.create(
            account_ruc=account_ruc, periodo_desde=desde, periodo_hasta=hasta,
            succeeded=False, error=str(exc)[:1000],
        )
        raise
    resultado = guardar(account_ruc, filas, constancias=constancias)
    derivar(account_ruc, resultado)
    ConsultaDeclaraciones.objects.create(
        account_ruc=account_ruc, periodo_desde=desde, periodo_hasta=hasta,
        filas=resultado.filas, nuevas=resultado.nuevas,
    )
    logger.info(
        "Declaraciones %s %s-%s: %d filas (%d nuevas, %d actualizadas), %d periodos declarados",
        account_ruc, desde, hasta, resultado.filas, resultado.nuevas, resultado.actualizadas,
        resultado.periodos_declarados,
    )
    return resultado


__all__ = [
    "ResultadoDeclaraciones", "alimentar_declarado", "alimentar_resultados", "derivar", "guardar", "normalizar",
    "rango_por_defecto", "registrar_evidencia", "sincronizar", "ultimo_periodo_cerrado",
    "vigentes_621",
]
