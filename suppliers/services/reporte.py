"""El informe de proveedores en PDF: la cartera, su estado en SUNAT y lo que
un auditor observaría, en un documento que se puede llevar al contador.

Es el mismo contenido que la pantalla —cartera, fiscalización simulada,
señales por proveedor, comprobantes de proveedores marcados—, pero entero y
en orden de lectura: primero qué hay en juego, después quién lo causa,
después el detalle. Se genera con reportlab (platypus) como las boletas de
planilla; sin plantillas HTML porque no hay un navegador en el worker que las
imprima.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

from ..models import Supplier
from .exposure import (
    CERO, comprobantes_en_riesgo, compras_por_proveedor, describir_comprobantes,
    resumen_riesgo,
)
from .fiscalizacion import Fiscalizacion, simular_fiscalizacion
from .ssco import rucs_en_padron

# Tope de filas de comprobantes marcados: el informe es para leerlo, no para
# sustituir a la exportación. Se avisa cuando se recorta.
TOPE_COMPROBANTES = 300

GRAVEDAD = {"critica": "Crítica", "alta": "Alta", "media": "Media", "baja": "Baja"}
NIVEL = {"alto": "Alto", "medio": "Medio", "bajo": "Bajo", "sin_senales": "—"}

TINTA = colors.HexColor("#1d1d1f")
GRIS = colors.HexColor("#6e6e73")
LINEA = colors.HexColor("#d9d9de")
FONDO = colors.HexColor("#f4f4f6")
ROJO = colors.HexColor("#b3261e")
ROJO_SUAVE = colors.HexColor("#fdebe9")
AMBAR = colors.HexColor("#8a5a00")


def _soles(valor: Decimal | None) -> str:
    if valor is None:
        return "—"
    entero = f"{int(valor):,}".replace(",", ".")
    return f"S/ {entero}"


def _fecha(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else "—"


def _estilos():
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("titulo", parent=base["Title"], fontSize=20, leading=24,
                                 alignment=0, spaceAfter=2 * mm, textColor=TINTA),
        "sub": ParagraphStyle("sub", parent=base["Normal"], fontSize=9.5, textColor=GRIS,
                              leading=13),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=13, leading=16,
                             spaceBefore=6 * mm, spaceAfter=2 * mm, textColor=TINTA),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=10.5, leading=13,
                             spaceBefore=3 * mm, spaceAfter=1 * mm, textColor=TINTA),
        "p": ParagraphStyle("p", parent=base["Normal"], fontSize=9, leading=12.5,
                            textColor=TINTA),
        "nota": ParagraphStyle("nota", parent=base["Normal"], fontSize=8, leading=11,
                               textColor=GRIS),
        "celda": ParagraphStyle("celda", parent=base["Normal"], fontSize=8, leading=10,
                                textColor=TINTA),
        "celda_der": ParagraphStyle("celda_der", parent=base["Normal"], fontSize=8,
                                    leading=10, alignment=TA_RIGHT, textColor=TINTA),
        "kpi_v": ParagraphStyle("kpi_v", parent=base["Normal"], fontSize=15, leading=18,
                                textColor=TINTA),
        "kpi_l": ParagraphStyle("kpi_l", parent=base["Normal"], fontSize=8, leading=10,
                                textColor=GRIS),
    }


def _tabla(cabecera: list[str], filas: list[list], anchos: list[float], estilos,
           numericas: set[int] = frozenset(), resaltar: list[int] | None = None) -> Table:
    def celda(texto, i):
        estilo = estilos["celda_der"] if i in numericas else estilos["celda"]
        if texto is None:
            texto = "—"
        return Paragraph(str(texto), estilo)

    datos = [[Paragraph(f"<b>{c}</b>", estilos["celda"]) for c in cabecera]]
    datos += [[celda(v, i) for i, v in enumerate(f)] for f in filas]
    tabla = Table(datos, colWidths=anchos, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), FONDO),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINEA),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINEA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for fila in resaltar or []:
        estilo.append(("BACKGROUND", (0, fila + 1), (-1, fila + 1), ROJO_SUAVE))
    tabla.setStyle(TableStyle(estilo))
    return tabla


def _kpis(pares: list[tuple[str, str]], estilos) -> Table:
    datos = [[
        Table([[Paragraph(v, estilos["kpi_v"])], [Paragraph(l, estilos["kpi_l"])]],
              colWidths=[(A4[0] - 30 * mm) / len(pares) - 3 * mm])
        for l, v in pares
    ]]
    tabla = Table(datos, colWidths=[(A4[0] - 30 * mm) / len(pares)] * len(pares))
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FONDO),
        ("BOX", (0, 0), (-1, -1), 0, FONDO),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tabla


def _pie(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GRIS)
    canvas.drawString(15 * mm, 10 * mm, "Empresario · Informe de proveedores")
    canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Página {doc.page}")
    canvas.restoreState()


def render_reporte(organization) -> bytes:
    """El PDF completo de la cartera de esta empresa."""
    account_ruc = organization.ruc
    estilos = _estilos()
    hoy = timezone.localdate()

    cartera = list(
        Supplier.objects.filter(account_ruc=account_ruc, is_tracked=True)
        .order_by("-has_issue", "alias", "business_name", "ruc")
    )
    compras = compras_por_proveedor(account_ruc)
    padron = rucs_en_padron(s.ruc for s in cartera)
    fiscalizacion: Fiscalizacion = simular_fiscalizacion(account_ruc)
    observados = {a.ruc: a for a in fiscalizacion.proveedores}
    riesgo = resumen_riesgo(account_ruc)

    for s in cartera:
        compra = compras.get(s.ruc)
        s.total_comprado = compra.total if compra else CERO
        s.n_comprobantes = compra.comprobantes if compra else 0

    # Por dinero, con los marcados primero: es el orden en que se leen.
    cartera.sort(key=lambda s: (s.has_issue, s.ruc in padron, s.total_comprado), reverse=True)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=18 * mm,
        title=f"Informe de proveedores {account_ruc}", author="Empresario",
    )
    ancho = A4[0] - 30 * mm
    cuerpo = []

    # ── Portada ──
    cuerpo.append(Paragraph("Informe de proveedores", estilos["titulo"]))
    cuerpo.append(Paragraph(
        f"<b>{organization.name or organization.ruc}</b> · RUC {account_ruc} · "
        f"generado el {hoy:%d/%m/%Y}", estilos["sub"],
    ))
    cuerpo.append(Spacer(1, 5 * mm))
    con_obs = sum(1 for s in cartera if s.has_issue)
    sin_verificar = sum(1 for s in cartera if s.last_checked_at is None)
    cuerpo.append(_kpis([
        ("Proveedores", str(len(cartera))),
        ("Con observaciones SUNAT", str(con_obs)),
        ("En padrón SSCO", str(sum(1 for s in cartera if s.ruc in padron))),
        ("Sin verificar", str(sin_verificar)),
        ("Contingencia estimada", _soles(fiscalizacion.contingencia_total)),
    ], estilos))
    cuerpo.append(Spacer(1, 3 * mm))
    cuerpo.append(Paragraph(
        "Este informe cruza la cartera de proveedores con su Ficha RUC pública, el "
        "padrón de Sujetos Sin Capacidad Operativa y los comprobantes electrónicos "
        "recibidos. Los importes de IGV, renta y multa son <b>estimaciones</b> para "
        "dimensionar la contingencia —no una liquidación— y no incluyen intereses ni "
        "gradualidad.", estilos["p"],
    ))

    # ── Fiscalización simulada ──
    cuerpo.append(Paragraph("1. Si SUNAT fiscalizara hoy", estilos["h2"]))
    cuerpo.append(_kpis([
        ("IGV que se discutiría", _soles(fiscalizacion.igv_en_riesgo)),
        ("Renta que se discutiría", _soles(fiscalizacion.renta_en_riesgo)),
        ("Multa (50 %)", _soles(fiscalizacion.multa_estimada)),
        ("Total estimado", _soles(fiscalizacion.contingencia_total)),
    ], estilos))
    cuerpo.append(Spacer(1, 2 * mm))
    cuerpo.append(Paragraph(
        f"Se analizaron {fiscalizacion.proveedores_analizados} emisores; "
        f"{fiscalizacion.proveedores_observados} muestran patrones que un auditor "
        f"cuestionaría, en {fiscalizacion.comprobantes_observados} comprobantes por "
        f"{_soles(fiscalizacion.total_observado)}.", estilos["p"],
    ))
    if fiscalizacion.por_senal:
        titulos = {
            "ssco": "Sujeto Sin Capacidad Operativa", "no_habido": "No habido",
            "baja_tras_facturar": "Baja tras facturar", "mismo_dia": "Facturas en ráfaga",
            "proveedor_reciente": "Recién inscrito", "actividad_ajena": "Actividad que no encaja",
            "correlativas": "Numeración correlativa", "cierre_ejercicio": "Compras de cierre",
            "montos_redondos": "Importes redondos",
        }
        filas = [[titulos.get(k, k), n] for k, n in
                 sorted(fiscalizacion.por_senal.items(), key=lambda kv: -kv[1])]
        cuerpo.append(Spacer(1, 2 * mm))
        cuerpo.append(_tabla(["Señal", "Proveedores"], filas, [ancho * 0.7, ancho * 0.3],
                             estilos, numericas={1}))

    # ── Cartera ──
    cuerpo.append(Paragraph("2. Cartera de proveedores", estilos["h2"]))
    if not cartera:
        cuerpo.append(Paragraph("No hay proveedores registrados.", estilos["p"]))
    else:
        filas, resaltar = [], []
        for i, s in enumerate(cartera):
            a = observados.get(s.ruc)
            if s.has_issue:
                resaltar.append(i)
            filas.append([
                f"{s.display_name}<br/><font color='#6e6e73'>{s.ruc}</font>",
                " · ".join(x for x in (s.status, s.condition) if x) or "Sin verificar",
                _fecha(s.started_activities_on or s.registered_on),
                _soles(s.total_comprado),
                s.n_comprobantes,
                NIVEL[a.nivel] if a else "—",
                "<font color='#b3261e'><b>Sí</b></font>" if s.ruc in padron else "",
            ])
        cuerpo.append(_tabla(
            ["Proveedor", "Estado SUNAT", "Inicio act.", "Comprado", "Fact.", "Riesgo", "SSCO"],
            filas, [ancho * 0.34, ancho * 0.2, ancho * 0.11, ancho * 0.13,
                    ancho * 0.07, ancho * 0.08, ancho * 0.07],
            estilos, numericas={3, 4}, resaltar=resaltar,
        ))
        cuerpo.append(Paragraph(
            "Sombreado: proveedores que SUNAT tiene marcados hoy (no habido, baja o "
            "suspensión).", estilos["nota"],
        ))

    # ── Detalle de observados ──
    cuerpo.append(PageBreak())
    cuerpo.append(Paragraph("3. Proveedores con señales, uno por uno", estilos["h2"]))
    if not fiscalizacion.proveedores:
        cuerpo.append(Paragraph(
            "Ningún proveedor muestra patrones que llamen la atención.", estilos["p"],
        ))
    for a in fiscalizacion.proveedores:
        bloque = [
            Paragraph(
                f"{a.proveedor} · RUC {a.ruc} · riesgo <b>{NIVEL[a.nivel]}</b>", estilos["h3"],
            ),
            Paragraph(
                " · ".join(filter(None, [
                    " / ".join(x for x in (a.estado, a.condicion) if x),
                    f"inscrito {_fecha(a.registrado_el)}" if a.registrado_el else "",
                    f"inicio {_fecha(a.inicio_actividades)}" if a.inicio_actividades else "",
                    a.actividad_principal.lower() if a.actividad_principal else "",
                ])) or "Sin ficha SUNAT todavía", estilos["nota"],
            ),
            Paragraph(
                f"Le compraste {_soles(a.total)} en {a.comprobantes} facturas "
                f"({_fecha(a.primera_compra)} – {_fecha(a.ultima_compra)}). En juego: "
                f"{_soles(a.igv_estimado)} de IGV y {_soles(a.renta_estimada)} de renta.",
                estilos["p"],
            ),
            _tabla(
                ["Gravedad", "Señal", "Detalle", "Fact.", "Importe"],
                [[GRAVEDAD[s.gravedad], s.titulo, s.detalle, s.comprobantes, _soles(s.importe)]
                 for s in a.senales],
                [ancho * 0.1, ancho * 0.2, ancho * 0.48, ancho * 0.08, ancho * 0.14],
                estilos, numericas={3, 4},
            ),
        ]
        cuerpo.append(KeepTogether(bloque))

    # ── Comprobantes de proveedores marcados ──
    comprobantes = comprobantes_en_riesgo(account_ruc)
    if riesgo.comprobantes:
        cuerpo.append(PageBreak())
        cuerpo.append(Paragraph("4. Comprobantes de proveedores marcados por SUNAT", estilos["h2"]))
        cuerpo.append(Paragraph(
            f"{riesgo.comprobantes} comprobantes de {riesgo.proveedores} proveedores por "
            f"{_soles(riesgo.total)}, con {_soles(riesgo.igv_estimado)} de IGV estimado. "
            f"{riesgo.confirmados} ya constaban marcados antes de la fecha de la factura.",
            estilos["p"],
        ))
        filas = [[
            f.proveedor, f.comprobante, _fecha(f.fecha), _soles(f.total),
            _soles(f.igv_estimado),
            "Ya estaba marcado" if f.confirmado_en_la_fecha else "Cayó después",
        ] for f in describir_comprobantes(account_ruc, comprobantes[:TOPE_COMPROBANTES])]
        cuerpo.append(_tabla(
            ["Proveedor", "Comprobante", "Fecha", "Total", "IGV est.", "En la fecha"],
            filas, [ancho * 0.3, ancho * 0.16, ancho * 0.11, ancho * 0.13, ancho * 0.12,
                    ancho * 0.18],
            estilos, numericas={3, 4},
        ))
        if riesgo.comprobantes > TOPE_COMPROBANTES:
            cuerpo.append(Paragraph(
                f"Se muestran los {TOPE_COMPROBANTES} más recientes de {riesgo.comprobantes}.",
                estilos["nota"],
            ))

    # ── Metodología ──
    cuerpo.append(Paragraph("Cómo se calcula", estilos["h2"]))
    for texto in (
        "<b>Estado SUNAT</b>: Ficha RUC pública (consulta RUC), consultada por la "
        "sincronización y guardada con historial diario por proveedor.",
        "<b>SSCO</b>: padrón de Sujetos Sin Capacidad Operativa publicado por SUNAT a fin "
        "de mes (D. Leg. 1532). Sus facturas no dan crédito fiscal ni gasto.",
        "<b>IGV estimado</b>: 18/118 del total del comprobante (la consulta CPE no trae "
        "el desglose). <b>Renta</b>: base imponible × 29,5 %. <b>Multa</b>: 50 % del "
        "tributo omitido (art. 178.1 del Código Tributario).",
        "<b>Señales</b>: patrones de facturación que SUNAT usa como indicios de "
        "operaciones no reales (art. 44 de la Ley del IGV). Son indicios, no un veredicto: "
        "cada uno puede tener explicación, y es esa explicación la que conviene tener "
        "documentada.",
    ):
        cuerpo.append(Paragraph(texto, estilos["nota"]))

    doc.build(cuerpo, onFirstPage=_pie, onLaterPages=_pie)
    return buffer.getvalue()


def nombre_reporte(organization) -> str:
    return f"proveedores-{organization.ruc}-{timezone.localdate():%Y%m%d}.pdf"


__all__ = ["render_reporte", "nombre_reporte"]
