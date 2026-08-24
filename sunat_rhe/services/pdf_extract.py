"""Reads a SUNAT-generated fee receipt (RHE) PDF into receipt fields.

The RHE PDF is produced by SUNAT itself, so its wording is stable:
"RECIBO POR HONORARIOS", "Nro: E001-8", "Total por honorarios:",
"Retención (8 %) IR:", "Fecha de emisión". Text extraction plus a few
anchored patterns beats OCR here — and when a scanned image arrives with
no text layer, the caller falls back to the pre-filled form.
"""

from __future__ import annotations

import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from pypdf import PdfReader

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}

_NUMBER = re.compile(r"Nro\.?\s*:?\s*([A-Z]\d{3})\s*-\s*(\d+)", re.I)
_RUC = re.compile(r"R\.?\s*U\.?\s*C\.?\s*:?\s*(\d{11})")
_DATE = re.compile(
    r"Fecha\s+de\s+emisi[oó]n\s*:?\s*(\d{1,2})\s+de\s+([A-Za-zñÑ]+)\s+del?\s+(\d{4})",
    re.I,
)
_GROSS = re.compile(
    r"Total\s+por\s+honorarios\s*:?\s*S?/?\.?\s*\(?([\d,]+\.\d{2})\)?", re.I
)
_WITHHELD = re.compile(
    r"Retenci[oó]n\s*\(?\s*\d*\s*%?\s*\)?\s*I\.?R\.?\s*:?\s*\(?([\d,]+\.\d{2})\)?",
    re.I,
)
_NET = re.compile(
    r"Total\s+Neto\s+Recibido\s*:?\s*S?/?\.?\s*\(?([\d,]+\.\d{2})\)?", re.I
)


class PdfExtractError(RuntimeError):
    """The file has no readable text layer (a scan) or is not a PDF."""


def _amount(match: re.Match | None) -> Decimal | None:
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def _issuer_name(text: str) -> str:
    """The issuer's name heads the PDF: first line of letters, before any
    address or the document body."""
    stop_words = ("recibo", "r.u.c", "ruc", "cal.", "av.", "jr.", "mz",
                  "telefono", "recibi de")
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 6 or any(ch.isdigit() for ch in line):
            continue
        folded = _fold(line)
        if any(word in folded for word in stop_words):
            continue
        if line == line.upper():
            return line
    return ""


def extract_fee_receipt(content: bytes, account_ruc: str) -> dict[str, Any]:
    """Best-effort field extraction; absent fields come back as None/""."""
    try:
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise PdfExtractError("El archivo no es un PDF legible.") from exc
    if not text.strip():
        raise PdfExtractError(
            "El PDF no tiene texto (parece un escaneo): completa el "
            "formulario con los datos a la vista."
        )

    number = _NUMBER.search(text)
    # The issuer's RUC is the one that is NOT the company's own.
    issuer_doc = next(
        (ruc for ruc in _RUC.findall(text) if ruc != account_ruc), ""
    )

    issue_date = None
    date = _DATE.search(text)
    if date:
        month = MONTHS.get(_fold(date.group(2)))
        if month:
            issue_date = f"{date.group(3)}-{month:02d}-{int(date.group(1)):02d}"

    gross = _amount(_GROSS.search(text))
    withheld = _amount(_WITHHELD.search(text))
    if gross is not None and withheld is None:
        net = _amount(_NET.search(text))
        if net is not None:
            withheld = gross - net

    return {
        "full_number": f"{number.group(1)}-{number.group(2)}" if number else "",
        "issuer_doc": issuer_doc,
        "issuer_name": _issuer_name(text),
        "issue_date": issue_date,
        "gross_amount": str(gross) if gross is not None else "",
        "income_tax_withheld": str(withheld) if withheld is not None else "0",
    }
