"""Parse the Consulta de ITF result HTML into ItfRecord field dicts."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup

from ..models import ItfSection

logger = logging.getLogger(__name__)

# Section title (as rendered) -> ItfSection value.
SECTION_TITLES = {
    "movimientos acumulados": ItfSection.ACCUMULATED,
    "movimientos de contra asientos por error": ItfSection.ERROR_REVERSAL,
    "exoneración, convenios e inafectación": ItfSection.EXEMPTION,
    "exoneracion, convenios e inafectacion": ItfSection.EXEMPTION,
}

# Header cell (lowercased) -> canonical model field. Unmapped columns go to `extra`.
HEADER_MAP = {
    "ruc": "declarant_ruc",
    "declarante": "declarant_name",
    "tipo doc": "doc_type",
    "período": "period",
    "periodo": "period",
    "tipo": "kind",
    "movimiento": "movement",
    "modalidad": "modality",
    "cod. oper.": "operation_code",
    "base": "base_amount",
    "impuesto": "tax",
    # "contribuyente" is the consulted RUC; it equals taxpayer_id, so it is not
    # remapped onto a field but kept in `extra` for traceability.
}
DECIMAL_FIELDS = {"base_amount", "tax"}


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _is_header(cells: list[str]) -> bool:
    lowered = {c.strip().lower() for c in cells}
    return "ruc" in lowered and ("período" in lowered or "periodo" in lowered)


def iter_records(html: str, taxpayer_id: str) -> Iterator[dict[str, Any]]:
    """Yield one ItfRecord field-dict per data row across the three sections."""
    soup = BeautifulSoup(html, "html.parser")
    current_section: str | None = None

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        first_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]

        # A single-cell table is a section banner; remember which section follows.
        if len(first_cells) == 1:
            title = first_cells[0].strip().lower()
            if title in SECTION_TITLES:
                current_section = SECTION_TITLES[title]
            continue

        if not _is_header(first_cells):
            continue
        if current_section is None:
            logger.warning("ITF data table found before any section title; skipping")
            continue

        headers = [c.strip() for c in first_cells]
        for row in rows[1:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if not cells or all(not c for c in cells):
                continue
            yield _map_row(headers, cells, current_section, taxpayer_id)


def _map_row(
    headers: list[str], cells: list[str], section: str, taxpayer_id: str
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "taxpayer_id": taxpayer_id,
        "section": section,
        "period": "",
        "declarant_ruc": "",
        "declarant_name": "",
        "doc_type": "",
        "kind": "",
        "movement": "",
        "modality": "",
        "operation_code": "",
        "base_amount": None,
        "tax": None,
        "extra": {},
        "raw": cells,
    }
    for header, value in zip(headers, cells):
        field = HEADER_MAP.get(header.lower())
        if field in DECIMAL_FIELDS:
            fields[field] = parse_decimal(value)
        elif field:
            fields[field] = value.strip()
        else:
            fields["extra"][header] = value.strip()
    return fields


def ytd_range(period: str) -> tuple[str, str]:
    """Year-to-date range ending at ``period`` (yyyymm): (yyyy01, period)."""
    return f"{period[:4]}01", period


def current_period(today: datetime.date | None = None) -> str:
    today = today or datetime.date.today()
    return f"{today.year}{today.month:02d}"


def previous_period(today: datetime.date | None = None) -> str:
    """The just-closed month as yyyymm (December rolls the year back)."""
    today = today or datetime.date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year}{month:02d}"
