"""Representación impresa de un comprobante a partir de su XML firmado.

SUNAT entrega por SOL solo el XML (Consultar Factura y Nota); el PDF que el
emisor manda por correo nunca pasa por ahí. Para quien necesita «ver» el
documento —el contador, una auditoría, el propio usuario— se arma aquí una
representación impresa con lo que el UBL declara: emisor y receptor, fechas,
líneas, totales por tipo de operación, IGV, forma de pago, detracción y el
documento que una nota modifica. El XML sigue siendo el comprobante con
validez legal, y el pie lo dice con su huella SHA-256.

Se genera con reportlab (platypus), como el informe de proveedores y las
boletas de planilla.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from finance_analytics.services.xml_extract import fix_mojibake, parse_invoice_xml

from ..models import DocumentClass, ElectronicInvoice

TITULO = {
    DocumentClass.INVOICE: "FACTURA ELECTRÓNICA",
    DocumentClass.CREDIT_NOTE: "NOTA DE CRÉDITO ELECTRÓNICA",
    DocumentClass.DEBIT_NOTE: "NOTA DE DÉBITO ELECTRÓNICA",
}

# Catálogo 03 de SUNAT: las unidades que más se ven, en palabras.
UNIDAD = {
    "NIU": "UND", "ZZ": "SERV", "KGM": "KG", "GRM": "G", "LTR": "L", "MTR": "M",
    "MTK": "M2", "MTQ": "M3", "BX": "CAJA", "PK": "PAQ", "DZN": "DOC", "SET": "SET",
    "HUR": "H", "DAY": "DÍA", "MON": "MES", "TNE": "T", "BG": "BOLSA", "BO": "BOT",
    "CEN": "CIEN", "MIL": "MIL",
}

# Catálogo 07 (afectación del IGV): a qué total de operaciones va cada línea.
_GRAVADA = {"10"}
_EXONERADA = {"20"}
_INAFECTA = {"30"}
_EXPORTACION = {"40"}
# 11–17, 21 y 31–37 son transferencias gratuitas (gravadas, exoneradas o
# inafectas): se informan aparte y no suman al total a pagar.

SIMBOLO = {"PEN": "S/", "USD": "US$", "EUR": "€"}

# Códigos de ítem que algunos emisores mandan cuando no tienen ninguno.
_SIN_CODIGO = {"", "0", "-", "--", "."}

TINTA = colors.HexColor("#1d1d1f")
GRIS = colors.HexColor("#6e6e73")
LINEA = colors.HexColor("#d9d9de")
FONDO = colors.HexColor("#f4f4f6")

_ANCHO = A4[0] - 30 * mm


# ------------------------------------------------------------- lectura XML
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter(root: ET.Element, name: str):
    for node in root.iter():
        if _local(node.tag) == name:
            yield node


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    return next((c for c in node if _local(c.tag) == name), None)


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except (InvalidOperation, ValueError):
        return None


def _party(root: ET.Element, block: str) -> dict[str, str]:
    """Nombre y RUC/DNI de AccountingSupplierParty / AccountingCustomerParty."""
    node = next(_iter(root, block), None)
    if node is None:
        return {"name": "", "id": ""}
    legal = next(_iter(node, "RegistrationName"), None)
    trade = next(_iter(node, "PartyName"), None)
    ident = next(_iter(node, "PartyIdentification"), None)
    name = _text(legal) or _text(_child(trade, "Name"))
    return {"name": fix_mojibake(name), "id": _text(_child(ident, "ID"))}


def _cabecera(xml_text: str) -> dict[str, Any]:
    """Lo que ``parse_invoice_xml`` no devuelve porque la analítica no lo
    necesita: nombres de las partes, hora, cuenta de detracción, descuentos
    y la fecha del documento que una nota modifica."""
    root = ET.fromstring(re.sub(r"<ds:Signature.*?</ds:Signature>", "", xml_text, flags=re.S))
    monetary = next(_iter(root, "LegalMonetaryTotal"), None) or next(
        _iter(root, "RequestedMonetaryTotal"), None
    )
    detraction_account = ""
    for means in _iter(root, "PaymentMeans"):
        if _text(_child(means, "ID")).lower() == "detraccion":
            detraction_account = _text(_child(_child(means, "PayeeFinancialAccount"), "ID"))
            break
    ref = next(_iter(root, "InvoiceDocumentReference"), None)
    tax_total = _decimal(_text(_child(next(_iter(root, "TaxTotal"), None), "TaxAmount")))
    return {
        "supplier": _party(root, "AccountingSupplierParty"),
        "customer": _party(root, "AccountingCustomerParty"),
        "issue_date": _text(_child(root, "IssueDate")),
        "issue_time": _text(_child(root, "IssueTime"))[:8],
        "allowance": _decimal(_text(_child(monetary, "AllowanceTotalAmount"))),
        "charge": _decimal(_text(_child(monetary, "ChargeTotalAmount"))),
        "prepaid": _decimal(_text(_child(monetary, "PrepaidAmount"))),
        "tax_total": tax_total,
        "detraction_account": detraction_account,
        "reference_date": _text(_child(ref, "IssueDate")),
        "reference_type": fix_mojibake(_text(_child(ref, "DocumentType"))),
    }


# --------------------------------------------------------------- formato
def _money(value: Decimal | str | None, currency: str) -> str:
    if value in (None, ""):
        return "—"
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return str(value)
    return f"{SIMBOLO.get(currency, currency)} {amount:,.2f}"


def _fecha(value: str | date | None) -> str:
    if not value:
        return "—"
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    try:
        return date.fromisoformat(value[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return value


def _cantidad(item: dict) -> str:
    quantity = _decimal(item.get("quantity") or "")
    if quantity is None:
        return "—"
    texto = f"{quantity.normalize():f}" if quantity == quantity.to_integral() else f"{quantity:.3f}".rstrip("0")
    unidad = UNIDAD.get(item.get("unit") or "", item.get("unit") or "")
    return f"{texto} {unidad}".strip()


def _estilos():
    base = getSampleStyleSheet()
    return {
        "emisor": ParagraphStyle("emisor", parent=base["Normal"], fontSize=12, leading=15,
                                 fontName="Helvetica-Bold", textColor=TINTA),
        "p": ParagraphStyle("p", parent=base["Normal"], fontSize=8.5, leading=11.5,
                            textColor=TINTA),
        "nota": ParagraphStyle("nota", parent=base["Normal"], fontSize=7.5, leading=10,
                               textColor=GRIS),
        "titulo": ParagraphStyle("titulo", parent=base["Normal"], fontSize=11, leading=14,
                                 fontName="Helvetica-Bold", alignment=1, textColor=TINTA),
        "numero": ParagraphStyle("numero", parent=base["Normal"], fontSize=13, leading=16,
                                 fontName="Helvetica-Bold", alignment=1, textColor=TINTA),
        "centro": ParagraphStyle("centro", parent=base["Normal"], fontSize=8.5, leading=11,
                                 alignment=1, textColor=GRIS),
        "celda": ParagraphStyle("celda", parent=base["Normal"], fontSize=8, leading=10,
                                textColor=TINTA),
        "celda_der": ParagraphStyle("celda_der", parent=base["Normal"], fontSize=8,
                                    leading=10, alignment=TA_RIGHT, textColor=TINTA),
        "total": ParagraphStyle("total", parent=base["Normal"], fontSize=9.5, leading=12,
                                fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=TINTA),
    }


def _p(texto: str, estilo) -> Paragraph:
    return Paragraph(escape(texto or ""), estilo)


# ----------------------------------------------------------------- bloques
def _encabezado(invoice: ElectronicInvoice, cab: dict, data: dict, estilos) -> Table:
    supplier = cab["supplier"]
    emisor = [
        _p(supplier["name"] or invoice.issuer_name or "Emisor", estilos["emisor"]),
        _p(data.get("supplier_address") or "", estilos["nota"]),
    ]
    cuadro = Table(
        [
            [_p(f"RUC {supplier['id'] or invoice.issuer_ruc}", estilos["centro"])],
            [_p(TITULO.get(invoice.document_class, "COMPROBANTE ELECTRÓNICO"), estilos["titulo"])],
            [_p(invoice.full_number or f"{invoice.series}-{invoice.number}", estilos["numero"])],
        ],
        colWidths=[_ANCHO * 0.34],
    )
    cuadro.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, TINTA),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    tabla = Table([[emisor, cuadro]], colWidths=[_ANCHO * 0.64, _ANCHO * 0.36])
    tabla.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (-1, -1), (-1, -1), 0),
    ]))
    return tabla


def _datos(invoice: ElectronicInvoice, cab: dict, data: dict, estilos) -> Table:
    customer = cab["customer"]
    receptor = customer["name"] or invoice.receiver_name or "—"
    doc_receptor = customer["id"] or invoice.receiver_ruc or "—"
    forma = {"Contado": "Contado", "Credito": "Crédito"}.get(data.get("payment_form") or "", "")
    filas = [
        ("Fecha de emisión", f"{_fecha(cab['issue_date'] or invoice.issue_date)}"
         + (f"  {cab['issue_time']}" if cab["issue_time"] else "")),
        ("Cliente" if invoice.direction == "emitida" else "Receptor", receptor),
        ("RUC / documento", doc_receptor),
    ]
    if data.get("customer_address"):
        filas.append(("Dirección", data["customer_address"]))
    if data.get("due_date"):
        filas.append(("Fecha de vencimiento", _fecha(data["due_date"])))
    filas.append(("Moneda", {"PEN": "Soles (PEN)", "USD": "Dólares (USD)"}.get(
        data.get("currency") or invoice.currency, data.get("currency") or invoice.currency or "—")))
    if forma:
        filas.append(("Forma de pago", forma))
    if data.get("order_reference"):
        filas.append(("Orden de compra", data["order_reference"]))
    if data.get("reference_id") or invoice.references_document:
        referencia = data.get("reference_id") or invoice.references_document
        tipo = cab["reference_type"].title() if cab["reference_type"] else "Documento"
        fecha = f" del {_fecha(cab['reference_date'])}" if cab["reference_date"] else ""
        filas.append(("Documento que modifica", f"{tipo} {referencia}{fecha}"))
    if data.get("reference_reason"):
        filas.append(("Motivo", data["reference_reason"]))

    tabla = Table(
        [[_p(k, estilos["nota"]), _p(v, estilos["p"])] for k, v in filas],
        colWidths=[_ANCHO * 0.22, _ANCHO * 0.78],
    )
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FONDO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tabla


def _lineas(items: list[dict], currency: str, estilos) -> Table:
    con_codigo = any((i.get("code") or "").strip() not in _SIN_CODIGO for i in items)
    cabecera = ["Cant.", "Descripción", "V. unitario", "V. venta"]
    anchos = [_ANCHO * 0.13, _ANCHO * 0.55, _ANCHO * 0.16, _ANCHO * 0.16]
    filas: list[list] = [[Paragraph(f"<b>{c}</b>", estilos["celda"]) for c in cabecera]]
    for item in items:
        descripcion = escape(item.get("description") or "—")
        if con_codigo and (item.get("code") or "").strip() not in _SIN_CODIGO:
            descripcion = f"<font color='#6e6e73'>{escape(item['code'])}</font> · {descripcion}"
        if item.get("affectation") and item["affectation"] not in _GRAVADA:
            descripcion += f" <font color='#6e6e73'>(afect. {escape(item['affectation'])})</font>"
        filas.append([
            _p(_cantidad(item), estilos["celda"]),
            Paragraph(descripcion, estilos["celda"]),
            _p(_money(item.get("unit_value"), currency), estilos["celda_der"]),
            _p(_money(item.get("amount"), currency), estilos["celda_der"]),
        ])
    tabla = Table(filas, colWidths=anchos, repeatRows=1)
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), FONDO),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINEA),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINEA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tabla


def _totales(invoice: ElectronicInvoice, cab: dict, data: dict, currency: str, estilos) -> Table:
    """Operaciones por tipo desde las líneas (catálogo 07); cuando el XML no
    trae afectación por línea, la base del IGV manda."""
    sumas = {"gravadas": Decimal("0"), "exoneradas": Decimal("0"), "inafectas": Decimal("0"),
             "exportacion": Decimal("0"), "gratuitas": Decimal("0")}
    con_afectacion = False
    for item in data.get("items") or []:
        amount = _decimal(item.get("amount") or "")
        code = item.get("affectation") or ""
        if amount is None or not code:
            continue
        con_afectacion = True
        if code in _GRAVADA:
            sumas["gravadas"] += amount
        elif code in _EXONERADA:
            sumas["exoneradas"] += amount
        elif code in _INAFECTA:
            sumas["inafectas"] += amount
        elif code in _EXPORTACION:
            sumas["exportacion"] += amount
        else:
            sumas["gratuitas"] += amount
    if not con_afectacion:
        sumas["gravadas"] = data.get("taxable_amount") or Decimal("0")

    igv = data.get("igv_amount")
    filas = [("Op. gravadas", sumas["gravadas"])]
    for etiqueta, clave in (("Op. exoneradas", "exoneradas"), ("Op. inafectas", "inafectas"),
                            ("Exportación", "exportacion"), ("Op. gratuitas", "gratuitas")):
        if sumas[clave]:
            filas.append((etiqueta, sumas[clave]))
    if cab["allowance"]:
        filas.append(("Descuentos", cab["allowance"]))
    if cab["charge"]:
        filas.append(("Otros cargos", cab["charge"]))
    filas.append(("IGV", igv if igv is not None else Decimal("0")))
    if cab["tax_total"] is not None and igv is not None and cab["tax_total"] - igv > Decimal("0.005"):
        filas.append(("Otros tributos", cab["tax_total"] - igv))
    if cab["prepaid"]:
        filas.append(("Anticipos", cab["prepaid"]))
    total = data.get("total_amount") if data.get("total_amount") is not None else invoice.total_amount

    datos = [[_p(k, estilos["celda"]), _p(_money(v, currency), estilos["celda_der"])] for k, v in filas]
    datos.append([_p("Importe total", estilos["total"]), _p(_money(total, currency), estilos["total"])])
    tabla = Table(datos, colWidths=[_ANCHO * 0.22, _ANCHO * 0.18], hAlign="RIGHT")
    tabla.setStyle(TableStyle([
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, TINTA),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tabla


def _pie(invoice: ElectronicInvoice):
    huella = invoice.xml_sha256 or "—"

    def dibujar(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GRIS)
        canvas.drawString(
            15 * mm, 13 * mm,
            "Representación impresa generada por EMPRESARIO a partir del XML firmado. "
            "El XML es el comprobante con validez legal.",
        )
        canvas.drawString(15 * mm, 9 * mm, f"SHA-256 del XML: {huella}")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Página {doc.page}")
        canvas.restoreState()

    return dibujar


# ------------------------------------------------------------------ público
def render_invoice_pdf(invoice: ElectronicInvoice) -> bytes:
    """El comprobante como PDF, leído de su XML firmado."""
    xml_text = invoice.xml_content
    data = parse_invoice_xml(xml_text)
    cab = _cabecera(xml_text)
    currency = data.get("currency") or invoice.currency or "PEN"
    estilos = _estilos()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=20 * mm,
        title=f"{TITULO.get(invoice.document_class, 'Comprobante')} {invoice.full_number}",
        author="Empresario",
    )
    cuerpo: list = [
        _encabezado(invoice, cab, data, estilos),
        Spacer(1, 4 * mm),
        _datos(invoice, cab, data, estilos),
        Spacer(1, 4 * mm),
    ]
    if invoice.is_cancelled or invoice.is_rejected:
        estado = "ANULADO" if invoice.is_cancelled else "RECHAZADO"
        cuerpo.append(Paragraph(
            f"<b>Comprobante {estado}</b> según SUNAT" + (f" ({escape(invoice.status)})" if invoice.status else ""),
            estilos["p"],
        ))
        cuerpo.append(Spacer(1, 2 * mm))

    items = data.get("items") or []
    if items:
        cuerpo.append(_lineas(items, currency, estilos))
        cuerpo.append(Paragraph("Importes sin IGV.", estilos["nota"]))
    else:
        cuerpo.append(Paragraph("El XML no declara líneas de detalle.", estilos["nota"]))
    cuerpo.append(Spacer(1, 3 * mm))
    cuerpo.append(_totales(invoice, cab, data, currency, estilos))
    cuerpo.append(Spacer(1, 3 * mm))

    notas = data.get("notes") or []
    leyenda = next((n for n in notas if n.upper().startswith("SON")), "")
    if leyenda:
        cuerpo.append(_p(leyenda, estilos["p"]))
    for cuota_idx, cuota in enumerate(data.get("installments") or [], start=1):
        cuerpo.append(_p(
            f"Cuota {cuota_idx}: {_money(cuota.get('amount'), currency)} · vence {_fecha(cuota.get('due_date'))}",
            estilos["p"],
        ))
    detraccion = data.get("detraction")
    if detraccion:
        texto = "Operación sujeta a detracción"
        if detraccion.get("percent"):
            texto += f" del {detraccion['percent']} %"
        if detraccion.get("amount"):
            texto += f" ({_money(detraccion['amount'], currency)})"
        if cab["detraction_account"]:
            texto += f" · Cuenta Banco de la Nación {cab['detraction_account']}"
        cuerpo.append(_p(texto, estilos["p"]))
    for nota in notas:
        if not nota.upper().startswith("SON") and "detracci" not in nota.lower():
            cuerpo.append(_p(nota, estilos["nota"]))

    doc.build(cuerpo, onFirstPage=_pie(invoice), onLaterPages=_pie(invoice))
    return buffer.getvalue()


def invoice_file_stem(invoice: ElectronicInvoice) -> str:
    """Nombre a la manera de SUNAT: RUC emisor, tipo (01/07/08), serie y
    número — inequívoco aunque una factura y una nota compartan serie."""
    tipo = invoice.cpe_code or invoice.document_type or "00"
    return f"{invoice.issuer_ruc}-{tipo}-{invoice.series}-{invoice.number}"


__all__ = ["render_invoice_pdf", "invoice_file_stem"]
