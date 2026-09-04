"""El recibo por honorarios como PDF.

Si el trabajador entregó el PDF de SUNAT y se registró subiéndolo, ese es
el archivo (lo sirve la vista tal cual). Si el recibo vino del scraping,
SUNAT solo dio la página de consulta: aquí se arma una representación con
lo mismo que imprime el recibo —emisor, número, «recibí de», concepto,
importes, retención y los pagos que el emisor registró—.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from payroll.services.payslip import amount_in_words

from ..models import FeeReceipt

TINTA = colors.HexColor("#1d1d1f")
GRIS = colors.HexColor("#6e6e73")
LINEA = colors.HexColor("#d9d9de")
FONDO = colors.HexColor("#f4f4f6")
ROJO = colors.HexColor("#b3261e")

_ANCHO = A4[0] - 30 * mm
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
]
SIMBOLO = {"PEN": "S/", "USD": "US$", "EUR": "€"}


def _money(value: Decimal | None, currency: str) -> str:
    if value is None:
        return "—"
    return f"{SIMBOLO.get(currency, currency)} {value.quantize(Decimal('0.01')):,.2f}"


def _fecha_larga(value: date | None) -> str:
    if not value:
        return "—"
    return f"{value.day} de {MESES[value.month - 1]} del {value.year}"


def _estilos():
    base = getSampleStyleSheet()
    return {
        "emisor": ParagraphStyle("emisor", parent=base["Normal"], fontSize=12, leading=15,
                                 fontName="Helvetica-Bold", textColor=TINTA),
        "p": ParagraphStyle("p", parent=base["Normal"], fontSize=9, leading=12.5,
                            textColor=TINTA),
        "nota": ParagraphStyle("nota", parent=base["Normal"], fontSize=7.5, leading=10,
                               textColor=GRIS),
        "titulo": ParagraphStyle("titulo", parent=base["Normal"], fontSize=11, leading=14,
                                 fontName="Helvetica-Bold", alignment=1, textColor=TINTA),
        "numero": ParagraphStyle("numero", parent=base["Normal"], fontSize=13, leading=16,
                                 fontName="Helvetica-Bold", alignment=1, textColor=TINTA),
        "centro": ParagraphStyle("centro", parent=base["Normal"], fontSize=8.5, leading=11,
                                 alignment=1, textColor=GRIS),
        "celda": ParagraphStyle("celda", parent=base["Normal"], fontSize=8.5, leading=11,
                                textColor=TINTA),
        "celda_der": ParagraphStyle("celda_der", parent=base["Normal"], fontSize=8.5,
                                    leading=11, alignment=TA_RIGHT, textColor=TINTA),
        "total": ParagraphStyle("total", parent=base["Normal"], fontSize=10, leading=13,
                                fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=TINTA),
        "alerta": ParagraphStyle("alerta", parent=base["Normal"], fontSize=10, leading=13,
                                 fontName="Helvetica-Bold", textColor=ROJO),
    }


def _p(texto: str, estilo) -> Paragraph:
    return Paragraph(escape(texto or ""), estilo)


def _pie(receipt: FeeReceipt):
    origen = "la consulta de recibos de SOL"
    if receipt.detail_fetched_at:
        origen += f" leída el {receipt.detail_fetched_at:%d/%m/%Y}"

    def dibujar(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GRIS)
        canvas.drawString(
            15 * mm, 13 * mm, f"Representación generada por EMPRESARIO a partir de {origen}.",
        )
        canvas.drawString(15 * mm, 9 * mm, "El recibo electrónico original lo custodia SUNAT.")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Página {doc.page}")
        canvas.restoreState()

    return dibujar


def render_receipt_pdf(receipt: FeeReceipt, company_name: str) -> bytes:
    """El recibo como PDF, con los datos de la fila y su detalle."""
    estilos = _estilos()
    detail = receipt.detail or {}
    currency = receipt.currency or "PEN"
    doc_tipo = "RUC" if len(receipt.issuer_doc) == 11 else "DNI"

    cuadro = Table(
        [
            [_p(f"{doc_tipo} {receipt.issuer_doc}", estilos["centro"])],
            [_p("RECIBO POR HONORARIOS ELECTRÓNICO", estilos["titulo"])],
            [_p(f"Nro: {receipt.full_number}", estilos["numero"])],
        ],
        colWidths=[_ANCHO * 0.36],
    )
    cuadro.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, TINTA),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    encabezado = Table(
        [[[_p(receipt.issuer_name or "Emisor", estilos["emisor"])], cuadro]],
        colWidths=[_ANCHO * 0.62, _ANCHO * 0.38],
    )
    encabezado.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (-1, -1), (-1, -1), 0),
    ]))

    gross = receipt.gross_amount or Decimal("0")
    withheld = receipt.income_tax_withheld or Decimal("0")
    net = receipt.net_amount if receipt.net_amount is not None else gross - withheld
    en_letras = detail.get("amount_in_words") or amount_in_words(gross)

    filas = [
        ("Recibí de", f"{company_name or receipt.account_ruc} · RUC {receipt.account_ruc}"),
        ("La suma de", en_letras),
    ]
    # SUNAT imprime «-» en los campos vacíos del recibo: no vale la pena
    # repetirlo.
    def valor(clave: str) -> str:
        texto = str(detail.get(clave) or "").strip()
        return "" if texto in ("", "-", "--") else texto

    for etiqueta, clave in (("Por concepto de", "concept"), ("Observación", "observation"),
                            ("Inciso", "clause"), ("Forma de pago", "payment_method")):
        if valor(clave):
            filas.append((etiqueta, valor(clave)))
    filas.append(("Fecha de emisión", _fecha_larga(receipt.issue_date)))
    datos = Table(
        [[_p(k, estilos["nota"]), _p(str(v), estilos["p"])] for k, v in filas],
        colWidths=[_ANCHO * 0.22, _ANCHO * 0.78],
    )
    datos.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FONDO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))

    importes = Table(
        [
            [_p("Total por honorarios", estilos["celda"]), _p(_money(gross, currency), estilos["celda_der"])],
            [_p("Retención (8 %) IR", estilos["celda"]), _p(f"({_money(withheld, currency)})", estilos["celda_der"])],
            [_p("Total neto recibido", estilos["total"]), _p(_money(net, currency), estilos["total"])],
        ],
        colWidths=[_ANCHO * 0.3, _ANCHO * 0.2], hAlign="RIGHT",
    )
    importes.setStyle(TableStyle([
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, TINTA),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))

    cuerpo: list = [encabezado, Spacer(1, 4 * mm)]
    if receipt.is_reverted:
        cuerpo += [_p(f"RECIBO REVERTIDO ({receipt.status})", estilos["alerta"]), Spacer(1, 2 * mm)]
    cuerpo += [datos, Spacer(1, 5 * mm), importes]

    pagos = detail.get("payments") or []
    if pagos:
        cuerpo += [Spacer(1, 5 * mm), _p("Pagos registrados por el emisor", estilos["nota"])]
        filas_pago = [[Paragraph(f"<b>{c}</b>", estilos["celda"])
                       for c in ("Fecha de pago", "Renta bruta", "Retención", "Neto pagado")]]
        for pago in pagos:
            filas_pago.append([
                _p(str(pago.get("date") or "—"), estilos["celda"]),
                _p(str(pago.get("gross") or "—"), estilos["celda_der"]),
                _p(str(pago.get("withheld") or "—"), estilos["celda_der"]),
                _p(str(pago.get("net") or "—"), estilos["celda_der"]),
            ])
        tabla = Table(filas_pago, colWidths=[_ANCHO * 0.25] * 4, repeatRows=1)
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), FONDO),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINEA),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINEA),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        cuerpo.append(tabla)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=18 * mm,
        title=f"Recibo por honorarios {receipt.full_number}", author="Empresario",
    )
    doc.build(cuerpo, onFirstPage=_pie(receipt), onLaterPages=_pie(receipt))
    return buffer.getvalue()


def receipt_file_stem(receipt: FeeReceipt) -> str:
    return f"RHE-{receipt.issuer_doc}-{receipt.full_number}"


__all__ = ["render_receipt_pdf", "receipt_file_stem"]
