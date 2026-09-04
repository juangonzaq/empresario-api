"""Maps a header-keyed results row onto FeeReceipt fields.

The client reads the receipts table with its header texts as keys, so
this module only has to answer one question per field: under which column
names does SUNAT publish it. If SUNAT renames a column, the sync logs the
unmapped headers and the fix is one alias here — no protocol work.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from bs4 import BeautifulSoup
from django.utils import timezone

from sunat_cpe.services.parsing import clean_field, parse_amount, parse_date

from .constants import TABLE_HEADER_WORDS

logger = logging.getLogger(__name__)


_DATA_ROW = re.compile(r"\d{2}/\d{2}/\d{4}")


def _grid(table) -> list[list[str]]:
    """Table rows as a rectangular grid, honoring rowspan and colspan —
    the results page groups its header in two rows («DATOS DEL EMISOR»
    over «NRO DE DOCUMENTO») and a flat read misaligns every column."""
    grid: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}
    for tr in table.find_all("tr", recursive=False) or table.find_all("tr"):
        row: list[str] = []
        col = 0
        cells = tr.find_all(["th", "td"], recursive=False)
        i = 0
        while i < len(cells) or col in pending:
            if col in pending:
                text, left = pending.pop(col)
                row.append(text)
                if left > 1:
                    pending[col] = (text, left - 1)
                col += 1
                continue
            cell = cells[i]
            i += 1
            text = cell.get_text(strip=True)
            try:
                rowspan = int(cell.get("rowspan") or 1)
                colspan = int(cell.get("colspan") or 1)
            except ValueError:
                rowspan = colspan = 1
            for _ in range(colspan):
                row.append(text)
                if rowspan > 1:
                    pending[col] = (text, rowspan - 1)
                col += 1
        grid.append(row)
    return grid


def rows_from_html(html: str) -> list[dict[str, str]]:
    """The receipts table of the results page, keyed by combined header.

    Only LEAF tables are considered (the page nests layout tables), the
    multi-row header collapses into one name per column, and data rows
    are the ones carrying a dd/mm/yyyy date. A period without receipts
    renders no qualifying table — empty, not an error."""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        if table.find("table") is not None:
            continue  # layout wrapper, not the data table
        text = _fold(table.get_text(" ", strip=True))
        if "renta bruta" not in text:
            continue
        grid = _grid(table)
        # The header is the row naming the amounts; the page also renders
        # a criteria recap (with dates!) ABOVE it, so anchoring on the
        # header — not on "first row with a date" — is what keeps recap
        # rows out of the data.
        header_idx = next(
            (i for i, row in enumerate(grid)
             if "renta bruta" in _fold(" ".join(row))
             and not any(_DATA_ROW.search(cell) for cell in row)),
            None,
        )
        if header_idx is None:
            continue
        header_rows = grid[max(0, header_idx - 1):header_idx + 1]
        width = max(len(row) for row in grid[header_idx:])
        headers = []
        for col in range(width):
            parts: list[str] = []
            for row in header_rows:
                if col < len(row) and row[col] and row[col] not in parts:
                    parts.append(row[col])
            headers.append(" · ".join(parts))
        out = []
        for row in grid[header_idx + 1:]:
            if not any(_DATA_ROW.search(cell) for cell in row):
                continue  # pagination / totals footer
            out.append({
                headers[i]: row[i]
                for i in range(min(len(headers), len(row)))
                if headers[i]
            })
        return out
    return []


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


# field → words that identify its column header (folded, substring
# match). Order MATTERS twice: fields claim headers in dict order, so the
# specific ones go first («nro de documento» must win before «nro»), and
# within a field the words are tried against the headers in table order.
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "issue_date": ("fecha de emision", "fecha"),
    "issuer_doc": ("nro de documento", "ruc", "dni"),
    # NOT «emisor»: the group header «DATOS DEL EMISOR» spans every
    # sub-column, tipo de documento included.
    "issuer_name": ("apellidos", "razon social", "denominacion"),
    "gross_amount": ("renta bruta", "bruta", "bruto", "importe", "monto", "total"),
    "income_tax_withheld": ("impuesto a la renta", "retencion", "ir "),
    "net_amount": ("renta neta", "neto"),
    "status": ("estado", "situacion"),
    "currency": ("moneda",),
    "series": ("serie",),
    "number": ("nro", "numero", "recibo", "correlativo"),
}

# Some headers match several fields ("Monto neto" hits gross and net); the
# order above is the priority, and a header already claimed stays claimed.
_REVERTED_WORDS = ("revertido", "anulado")

# SUNAT prints the currency as a word; the model stores ISO codes.
_CURRENCIES = {"soles": "PEN", "dolares": "USD", "euros": "EUR"}


def _currency(value: str) -> str:
    folded = _fold(clean_field(value))
    if not folded:
        return "PEN"
    return _CURRENCIES.get(folded, clean_field(value).upper()[:3])


def _assign_columns(headers: list[str]) -> dict[str, str]:
    """header text → field name, first-match wins by field priority.

    Only the SUB-header (after the « · » the grid builder adds) counts:
    the group name «IMPORTES DEL RECIBO EN MONEDA DE EMISIÓN» would
    otherwise make every amount column match «importe» and «moneda»."""
    folded = {header: _fold(header.split(" · ")[-1]) for header in headers}
    assigned: dict[str, str] = {}
    taken: set[str] = set()
    for fieldname, words in HEADER_ALIASES.items():
        for header, text in folded.items():
            if header in taken:
                continue
            if any(word in text for word in words):
                assigned[header] = fieldname
                taken.add(header)
                break
    unmapped = [h for h in headers if h not in assigned and h.strip()]
    if unmapped:
        logger.info("RHE: columnas sin mapear: %s", unmapped)
    return assigned


# Detail-page labels → detail dict keys. The page mirrors the printed
# receipt: the value follows its label in the text flow.
_DETAIL_LABELS = {
    "forma de pago": "payment_method",
    "la suma de": "amount_in_words",
    "por concepto de": "concept",
    "observacion": "observation",
    "inciso": "clause",
}


def detail_from_html(html: str) -> dict[str, Any]:
    """The receipt's detail page (accion=CapturaCriterioConsultaRec2):
    concept, payment method, observation, clause and the payments list."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [
        line.strip() for line in soup.get_text("\n", strip=True).splitlines()
        if line.strip()
    ]
    detail: dict[str, Any] = {}
    for i, line in enumerate(lines[:-1]):
        key = _DETAIL_LABELS.get(_fold(line).rstrip(":").strip())
        if key and key not in detail:
            value = lines[i + 1].strip()
            if value != ":":
                detail[key] = value

    payments = []
    for table in soup.find_all("table"):
        if table.find("table") is not None:
            continue
        if "fecha de pago" not in _fold(table.get_text(" ", strip=True)):
            continue
        grid = _grid(table)
        header_idx = next(
            (i for i, row in enumerate(grid)
             if "fecha de pago" in _fold(" ".join(row))),
            None,
        )
        if header_idx is None:
            continue
        headers = [_fold(h) for h in grid[header_idx]]
        for row in grid[header_idx + 1:]:
            if not any(_DATA_ROW.search(cell) for cell in row):
                continue
            paid = dict(zip(headers, row))
            payments.append({
                "date": paid.get("fecha de pago", ""),
                "gross": paid.get("renta bruta pagada", ""),
                "withheld": paid.get("retencion", ""),
                "net": paid.get("monto neto pagado", ""),
            })
        break
    if payments:
        detail["payments"] = payments
    return detail


def rows_to_fields(
    rows: list[dict[str, str]], account_ruc: str,
) -> list[dict[str, Any]]:
    """Header-keyed rows → FeeReceipt field dicts."""
    if not rows:
        return []
    # Keys starting with «__» are the client's annotations (detail, page),
    # not SUNAT columns: they never map to a field nor land in ``raw``.
    columns = _assign_columns(
        [key for key in rows[0] if not key.startswith("__")]
    )
    out = []
    for row in rows:
        detail = row.pop("__detail__", None)
        row.pop("__detail_html__", None)
        values: dict[str, str] = {}
        for header, value in row.items():
            fieldname = columns.get(header)
            if fieldname:
                values[fieldname] = value
        fields = _fields(values, row, account_ruc)
        if detail:
            fields["detail"] = detail
            fields["detail_fetched_at"] = timezone.now()
        out.append(fields)
    return out


def _fields(
    values: dict[str, str], raw: dict[str, str], account_ruc: str,
) -> dict[str, Any]:
    issue_date = parse_date(clean_field(values.get("issue_date")))
    period = f"{issue_date.year}{issue_date.month:02d}" if issue_date else ""
    status = clean_field(values.get("status"))
    series = clean_field(values.get("series"))
    number = clean_field(values.get("number"))
    # Some layouts publish "E001-245" in one column: split it when the
    # dedicated series column is missing.
    if not series and "-" in number:
        series, _, number = number.partition("-")
    # SUNAT pads correlatives ("00000008"); the PDF prints "8". Normalize
    # so a scraped receipt MERGES with one registered from the paper
    # instead of duplicating it.
    number = number.lstrip("0") or "0"
    doc = clean_field(values.get("issuer_doc"))
    if len(doc) == 11:
        doc_type = "6"  # RUC
    elif len(doc) == 8:
        doc_type = "1"  # DNI
    else:
        doc_type = ""

    return {
        "account_ruc": account_ruc,
        "issuer_doc": doc,
        "issuer_doc_type": doc_type,
        "issuer_name": clean_field(values.get("issuer_name")),
        "series": series,
        "number": number,
        "full_number": f"{series}-{number}".strip("-"),
        "issue_date": issue_date,
        "period": period,
        "currency": _currency(values.get("currency") or ""),
        "gross_amount": parse_amount(values.get("gross_amount")),
        "income_tax_withheld": parse_amount(values.get("income_tax_withheld")),
        "net_amount": parse_amount(values.get("net_amount")),
        "status": status,
        # «NO ANULADO» contains «anulado»: only a status that BEGINS with
        # the word means the receipt is out.
        "is_reverted": _fold(status).startswith(_REVERTED_WORDS),
        "raw": raw,
        "last_seen_at": timezone.now(),
    }
