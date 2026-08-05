"""Proposal/report TXT → dicts.

The v30/v22 manuals ship no positional column layout for the proposal TXT
(docs/DESVIACIONES.md); the files come pipe-delimited with a header row, so
columns are mapped by header keywords and anything unmapped is preserved in
``raw_extra``. If SUNAT renames a header, the data lands in raw_extra instead
of being lost, and the mapping below gets a new alias.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# Canonical field -> lowercase keywords searched inside each header cell.
# First header matching any keyword wins; aliases come from the SIRE web UI
# and the manuals' evidence screenshots.
PROPOSAL_HEADER_MAP: dict[str, tuple[str, ...]] = {
    "tax_period": ("periodo",),
    "car_sunat": ("car sunat", "car_sunat", "nro car"),
    "issue_date": ("fecha de emis", "fecha emis", "fec. emis"),
    "doc_type": ("tipo cp", "tipo de cp", "tipo comprobante", "tipo cdp", "tipo doc"),
    "series": ("serie",),
    "number": ("nro cp", "num cp", "nro. cp", "numero final", "nro doc", "número cp"),
    "party_doc": ("nro doc identidad", "num doc identidad", "ruc", "doc. identidad"),
    "party_name": ("razon social", "razón social", "apellidos y nombres", "nombre"),
    "base_amount": ("bi gravada", "base imponible", "valor facturado", "valor fact"),
    "igv": ("igv", "ipm"),
    "total": ("total cp", "importe total", "total comprobante"),
}

DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned in {"-", "0.00-"}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _build_column_map(headers: list[str]) -> dict[int, str]:
    """Index -> canonical field, first keyword match wins, one column per field."""
    assigned: dict[int, str] = {}
    taken: set[str] = set()
    for field, keywords in PROPOSAL_HEADER_MAP.items():
        for index, header in enumerate(headers):
            lowered = header.strip().lower()
            if index in assigned or field in taken:
                continue
            if any(keyword in lowered for keyword in keywords):
                assigned[index] = field
                taken.add(field)
                break
    return assigned


def iter_proposal_rows(text: str) -> Iterator[dict[str, Any]]:
    """Yield one normalized dict per proposal TXT line.

    Output keys: the canonical fields above (missing ones -> None) plus
    ``raw_extra`` with every unmapped column keyed by its original header.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return
    headers = lines[0].split("|")
    column_map = _build_column_map(headers)
    if not column_map:
        logger.warning(
            "Proposal TXT header did not match any known column: %r", lines[0][:200]
        )
    for line in lines[1:]:
        cells = line.split("|")
        row: dict[str, Any] = {field: None for field in PROPOSAL_HEADER_MAP}
        raw_extra: dict[str, str] = {}
        for index, cell in enumerate(cells):
            field = column_map.get(index)
            if field:
                row[field] = cell.strip()
            else:
                header = headers[index].strip() if index < len(headers) else f"col{index}"
                if cell.strip():
                    raw_extra[header] = cell.strip()
        row["issue_date"] = parse_date(row["issue_date"])
        for money in ("base_amount", "igv", "total"):
            row[money] = parse_decimal(row[money])
        row["raw_extra"] = raw_extra
        # A row without series+number is a totals/footer line, not a document.
        if row["series"] or row["number"]:
            yield row


def iter_boxes(text: str) -> Iterator[tuple[str, Decimal]]:
    """Yield (box_number, amount) pairs from the casillas TXT report.

    The report is loosely formatted; any line containing a 3-digit box code
    from Anexo V followed by an amount is captured.
    """
    pattern = re.compile(r"\b(1[0-9]{2})\b")
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        box = next((m.group(1) for cell in cells[:3] if (m := pattern.search(cell))), None)
        if not box:
            continue
        amount = next(
            (value for cell in reversed(cells) if (value := parse_decimal(cell)) is not None),
            None,
        )
        if amount is not None:
            yield box, amount


def iter_statistics(text: str) -> Iterator[dict[str, Any]]:
    """Yield rows of the statistics report: Razón Social|Monto|Porcentaje."""
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines:
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 2:
            continue
        amount = parse_decimal(cells[1].replace(" ", ""))
        if amount is None:  # header or malformed line
            continue
        yield {"name": cells[0], "amount": amount, "share": cells[2] if len(cells) > 2 else ""}


def decode_report_text(payload: bytes) -> str:
    """SUNAT TXT files come in Latin-1 more often than UTF-8."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")
