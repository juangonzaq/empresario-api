"""Turns SUNAT's section HTML into structured data."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup, NavigableString

from .constants import KIND_BOOLEAN, NO_DATA_MARKERS, SectionSpec

# The yes/no sections phrase their answer right after the question.
BOOLEAN_ANSWER = re.compile(r"UIT\s*\?\s*(S[IÍ]|NO)\b", re.IGNORECASE)


def clean(text: Any) -> str:
    """Collapse whitespace and undo SUNAT's HTML entities.

    Some pages arrive double-encoded (``&#191;`` reaching the text layer literally),
    so unescaping twice is what actually yields readable text.
    """
    if text is None:
        return ""
    return " ".join(html.unescape(html.unescape(str(text))).split()).strip()


@dataclass
class Table:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"headers": self.headers, "rows": self.rows}

    def as_records(self) -> list[dict[str, str]]:
        """Rows keyed by header, for the tables that map onto models."""
        return [
            dict(zip(self.headers, row))
            for row in self.rows
            if len(row) >= len(self.headers)
        ]


@dataclass
class SectionData:
    """A parsed section response."""

    key: str
    label: str
    title: str = ""
    tables: list[Table] = field(default_factory=list)
    text: str = ""
    has_data: bool = False
    answer: bool | None = None  # only for yes/no sections

    def table_payload(self) -> list[dict[str, Any]]:
        return [table.as_dict() for table in self.tables]


def _is_layout_table(table) -> bool:
    """SUNAT nests data tables inside layout ones; only the innermost carry data."""
    return table.find("table") is not None


def _cell_text(cell) -> str:
    # Comment subclasses NavigableString and SUNAT leaves comments in the markup,
    # so `type(...) is` rather than isinstance.
    parts = (str(node) for node in cell.descendants if type(node) is NavigableString)
    return clean(" ".join(parts))


def extract_tables(soup: BeautifulSoup) -> list[Table]:
    tables: list[Table] = []
    for element in soup.find_all("table"):
        if _is_layout_table(element):
            continue
        rows = [
            [_cell_text(cell) for cell in tr.find_all(["th", "td"])]
            for tr in element.find_all("tr")
        ]
        rows = [row for row in rows if any(cell for cell in row)]
        if not rows:
            continue

        # A header row is the one made of <th>, else the first row.
        first_tr = element.find("tr")
        has_th = bool(first_tr and first_tr.find("th"))
        headers = rows[0] if (has_th or len(rows) > 1) else []
        body = rows[1:] if headers else rows
        tables.append(Table(headers=headers, rows=body))
    return tables


def is_no_data_row(row: list[str]) -> bool:
    """SUNAT fills an otherwise empty table with a single 'nothing here' row."""
    joined = " ".join(row).lower()
    return any(marker in joined for marker in NO_DATA_MARKERS)


def drop_empty(tables: list[Table]) -> list[Table]:
    """Strip the placeholder rows, then the tables left with nothing.

    Filtering per row matters: a section like *Información histórica* mixes tables
    that hold data with tables that say "No hay Información", and judging the whole
    page by its text would throw the real rows away.
    """
    kept = []
    for table in tables:
        table.rows = [row for row in table.rows if not is_no_data_row(row)]
        if table.rows:
            kept.append(table)
    return kept


def parse_section(spec: SectionSpec, page: str) -> SectionData:
    soup = BeautifulSoup(page, "html.parser")

    heading = soup.find(["h3", "h4"])
    stripped = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    text = clean(re.sub(r"<[^>]+>", " ", stripped))
    tables = extract_tables(soup)

    data = SectionData(
        key=spec.key,
        label=spec.label,
        title=clean(heading.get_text(" ")) if heading else "",
        tables=tables,
        text=text,
    )

    if spec.kind == KIND_BOOLEAN:
        match = BOOLEAN_ANSWER.search(text)
        data.answer = match.group(1).upper().startswith("S") if match else None
        data.has_data = bool(data.answer)
    else:
        data.tables = drop_empty(tables)
        data.has_data = bool(data.tables)
    return data


def parse_worker_rows(data: SectionData) -> list[dict[str, Any]]:
    """``Período | N° de Trabajadores | N° de Pensionistas | N° de Prestadores``."""
    rows: list[dict[str, Any]] = []
    for table in data.tables:
        for row in table.rows:
            if len(row) < 4 or not re.fullmatch(r"\d{4}-\d{2}", row[0]):
                continue
            rows.append({
                "period": row[0],
                "workers": _to_int(row[1]),
                "pensioners": _to_int(row[2]),
                "service_providers": _to_int(row[3]),
            })
    return rows


def parse_legal_representatives(data: SectionData) -> list[dict[str, Any]]:
    """``Documento | Nro. Documento | Nombre | Cargo | Fecha Desde``."""
    rows: list[dict[str, Any]] = []
    for table in data.tables:
        for row in table.rows:
            if len(row) < 5 or not row[1] or not row[2]:
                continue
            rows.append({
                "document_type": row[0][:40],
                "document_number": row[1][:20],
                "full_name": row[2][:255],
                "role": row[3][:120],
                "since": row[4][:20],
            })
    return rows


def _to_int(value: str) -> int | None:
    try:
        return int(re.sub(r"[^\d-]", "", value))
    except (ValueError, TypeError):
        return None
