"""Payslip PDF (spec §8.1), generated server-side with reportlab.

The canvas runs in invariant mode: no timestamps, no random IDs, so a
closed period reprints byte-identical from its snapshots — the property
that lets anyone verify a payslip from eight months ago against what was
actually paid.
"""

from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas

from ..models import ConceptKind, PayrollEntry

MONTHS_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

UNITS = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
TEENS = ["diez", "once", "doce", "trece", "catorce", "quince", "dieciséis",
         "diecisiete", "dieciocho", "diecinueve"]
TENS = ["", "", "veinte", "treinta", "cuarenta", "cincuenta", "sesenta",
        "setenta", "ochenta", "noventa"]
HUNDREDS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos",
            "quinientos", "seiscientos", "setecientos", "ochocientos",
            "novecientos"]


def _under_hundred(n: int) -> str:
    if n < 10:
        return UNITS[n]
    if n < 20:
        return TEENS[n - 10]
    if n < 30:
        return {20: "veinte", 21: "veintiuno", 22: "veintidós", 23: "veintitrés",
                24: "veinticuatro", 25: "veinticinco", 26: "veintiséis",
                27: "veintisiete", 28: "veintiocho", 29: "veintinueve"}[n]
    tens, units = divmod(n, 10)
    return TENS[tens] + (f" y {UNITS[units]}" if units else "")


def _under_thousand(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "cien"
    hundreds, rest = divmod(n, 100)
    words = HUNDREDS[hundreds]
    if rest:
        words = f"{words} {_under_hundred(rest)}".strip()
    return words


def amount_in_words(amount: Decimal) -> str:
    """'1234.56' → 'MIL DOSCIENTOS TREINTA Y CUATRO CON 56/100 SOLES'."""
    integer = int(amount)
    cents = int((amount - integer) * 100)
    if integer == 0:
        words = "cero"
    else:
        millions, rest = divmod(integer, 1_000_000)
        thousands, units = divmod(rest, 1000)
        parts = []
        if millions:
            parts.append(
                "un millón" if millions == 1
                else f"{_under_thousand(millions)} millones"
            )
        if thousands:
            parts.append(
                "mil" if thousands == 1 else f"{_under_thousand(thousands)} mil"
            )
        if units:
            parts.append(_under_thousand(units))
        words = " ".join(parts)
    return f"{words} con {cents:02d}/100 soles".upper()


def format_pen(value: Decimal) -> str:
    """es-PE: thousands with dot, decimals with comma (§7.5)."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    integer = int(value)
    cents = int(round((value - integer) * 100))
    grouped = f"{integer:,}".replace(",", ".")
    return f"{sign}S/ {grouped},{cents:02d}"


class _InvariantCanvas(pdf_canvas.Canvas):
    """Reproducible output: fixed metadata, no timestamps."""

    def __init__(self, *args, **kwargs):
        kwargs["invariant"] = 1
        super().__init__(*args, **kwargs)


REGIME_LABEL = {"afp": "AFP", "onp": "ONP", "sin_regimen": "Sin régimen"}

BLOCKS = [
    (ConceptKind.EARNING, "INGRESOS"),
    (ConceptKind.DEDUCTION, "DESCUENTOS DEL TRABAJADOR"),
    (ConceptKind.EMPLOYER_COST, "APORTES DEL EMPLEADOR"),
]


def render_payslip(entry: PayrollEntry, company_name: str) -> bytes:
    """One employee's payslip for one period, as PDF bytes."""
    period = entry.period
    colaborador = entry.colaborador
    buffer = io.BytesIO()
    page = _InvariantCanvas(buffer, pagesize=A4)
    width, height = A4
    margin = 18 * mm
    y = height - margin

    def line(text: str, size=9, bold=False, dy=4.6 * mm, x=margin):
        nonlocal y
        page.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        page.drawString(x, y, text)
        y -= dy

    # ------------------------------------------------------------ header
    line(company_name or period.taxpayer_id, 13, bold=True, dy=5.5 * mm)
    line(f"RUC {period.taxpayer_id}", 9, dy=7 * mm)
    page.setFont("Helvetica-Bold", 12)
    page.drawCentredString(width / 2, y, "BOLETA DE PAGO DE REMUNERACIONES")
    y -= 5 * mm
    page.setFont("Helvetica", 9)
    page.drawCentredString(
        width / 2, y,
        f"Periodo: {MONTHS_ES[period.month - 1]} de {period.year}",
    )
    y -= 8 * mm

    # ------------------------------------------------------ worker block
    rows = [
        ("Trabajador", colaborador.full_name),
        ("Documento", f"{colaborador.document_type} {colaborador.document_number}"),
        ("Cargo", colaborador.position or "—"),
        ("Fecha de ingreso",
         colaborador.hired_on.strftime("%d/%m/%Y") if colaborador.hired_on else "—"),
        ("Régimen pensionario",
         f"{REGIME_LABEL.get(entry.pension_regime, entry.pension_regime)}"
         + (f" · {entry.pension_fund}" if entry.pension_fund else "")),
        ("CUSPP", entry.cuspp or "—"),
        ("Cuenta de abono",
         f"{colaborador.bank_name} {colaborador.bank_account_number}".strip() or "—"),
    ]
    for label, value in rows:
        page.setFont("Helvetica-Bold", 8.5)
        page.drawString(margin, y, f"{label}:")
        page.setFont("Helvetica", 8.5)
        page.drawString(margin + 40 * mm, y, str(value))
        y -= 4.4 * mm
    y -= 2 * mm

    # -------------------------------------------------------- attendance
    attendance = [
        ("Días trabajados", entry.worked_days),
        ("Vacaciones", entry.vacation_days),
        ("D. médico", entry.sick_leave_days),
        ("Licencias", entry.leave_days),
        ("Faltas", entry.absence_days),
        ("H.E. 25 %", entry.first_overtime_hours),
        ("H.E. 35 %", entry.additional_overtime_hours),
        ("Horas totales", entry.total_hours),
    ]
    page.setFont("Helvetica-Bold", 8)
    x = margin
    for label, _ in attendance:
        page.drawString(x, y, label)
        x += 22 * mm
    y -= 4 * mm
    page.setFont("Helvetica", 8)
    x = margin
    for _, value in attendance:
        page.drawString(x, y, str(value))
        x += 22 * mm
    y -= 7 * mm

    # ------------------------------------------------------------ blocks
    lines = list(entry.lines.select_related("concept"))
    for kind, title in BLOCKS:
        block = [l for l in lines if l.concept.kind == kind and l.amount != 0]
        if not block:
            continue
        page.setFillColor(colors.HexColor("#eeeeee"))
        page.rect(margin, y - 1.5 * mm, width - 2 * margin, 5.5 * mm,
                  fill=1, stroke=0)
        page.setFillColor(colors.black)
        page.setFont("Helvetica-Bold", 9)
        page.drawString(margin + 2 * mm, y, title)
        y -= 6 * mm
        subtotal = Decimal("0")
        for row in block:
            page.setFont("Helvetica", 8.5)
            page.drawString(margin + 2 * mm, y, row.concept.name)
            if row.quantity is not None:
                page.drawString(margin + 75 * mm, y, str(row.quantity))
            page.drawRightString(width - margin - 2 * mm, y, format_pen(row.amount))
            subtotal += row.amount
            y -= 4.4 * mm
        page.setFont("Helvetica-Bold", 8.5)
        page.drawString(margin + 2 * mm, y, "Total")
        page.drawRightString(width - margin - 2 * mm, y, format_pen(subtotal))
        y -= 7 * mm

    # ------------------------------------------------------------- net
    page.setFillColor(colors.HexColor("#dddddd"))
    page.rect(margin, y - 2 * mm, width - 2 * margin, 7 * mm, fill=1, stroke=0)
    page.setFillColor(colors.black)
    page.setFont("Helvetica-Bold", 10.5)
    page.drawString(margin + 2 * mm, y, "NETO A PAGAR")
    page.drawRightString(width - margin - 2 * mm, y, format_pen(entry.net_pay))
    y -= 6.5 * mm
    page.setFont("Helvetica-Oblique", 8)
    page.drawString(margin, y, f"Son: {amount_in_words(entry.net_pay)}")
    y -= 12 * mm

    # ------------------------------------------------------------ footer
    footer = period.settings_snapshot.get("payslip_footer_text") or ""
    if footer:
        page.setFont("Helvetica", 7.5)
        page.drawString(margin, y, footer[:150])
        y -= 10 * mm
    y = max(y, 30 * mm)
    page.setFont("Helvetica", 8)
    page.line(margin, y, margin + 55 * mm, y)
    page.line(width - margin - 55 * mm, y, width - margin, y)
    y -= 4 * mm
    page.drawString(margin, y, "Empleador")
    page.drawRightString(width - margin, y, "Trabajador")

    page.showPage()
    page.save()
    return buffer.getvalue()


def payslip_filename(entry: PayrollEntry) -> str:
    period = entry.period
    return (
        f"boleta_{entry.colaborador.document_number}"
        f"_{period.year}-{period.month:02d}.pdf"
    )
