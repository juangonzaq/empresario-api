"""Parsing of SUNAFIL casilla pages."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from bs4 import BeautifulSoup

from .client import ListingPage
from .constants import IDENTITY_COLUMNS, READ_STATUS, SESSION_EXPIRED_MARKER, ListingSpec

DATETIME_FORMATS = ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S")
DATE_FORMATS = ("%d/%m/%Y",)


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(html.unescape(str(value)).split()).strip()


def parse_datetime(value: str) -> datetime | None:
    text = clean(value)
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text[:16], fmt)
        except ValueError:
            continue
    return None


def parse_date(value: str) -> date | None:
    text = clean(value)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_int(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", clean(value))
    return int(digits) if digits else None


def parse_listing(spec: ListingSpec, page: str) -> ListingPage:
    soup = BeautifulSoup(page, "html.parser")
    table = soup.find("table", id=spec.table_id)

    result = ListingPage(spec=spec)
    view_state = soup.find("input", {"name": "javax.faces.ViewState"})
    result.view_state = view_state["value"] if view_state else ""
    if table is None:
        return result

    rows = table.find_all("tr")
    if not rows:
        return result

    result.headers = [clean(cell.get_text(" "))
                      for cell in rows[0].find_all(["th", "td"])]
    for tr in rows[1:]:
        cells = tr.find_all("td")
        values = [clean(cell.get_text(" ")) for cell in cells]
        # Every page embeds a hidden "session expired" modal table.
        if not any(values) or SESSION_EXPIRED_MARKER in " ".join(values):
            continue
        result.rows.append(values)
        button = tr.find("button")
        result.detail_button_ids.append(button["id"] if button else "")
    return result


def identity_key(spec: ListingSpec, record: dict[str, str]) -> str:
    """A stable key for a row, so re-runs update instead of duplicating.

    Built from the columns that identify the item; hashed because SUNAFIL's subject
    lines are long and the record numbers carry slashes.
    """
    columns = IDENTITY_COLUMNS.get(spec.kind, ())
    parts = [clean(record.get(column, "")) for column in columns]
    if not any(parts):
        # Fall back to the whole row rather than collapsing everything onto one key.
        parts = [clean(value) for value in record.values()]
    raw = f"{spec.kind}|" + "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class DetailContent:
    """The body of an orientation invitation."""

    text: str = ""
    body_html: str = ""
    links: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


def parse_orientation_detail(page: str, form_id: str) -> DetailContent:
    soup = BeautifulSoup(page, "html.parser")
    form = soup.find("form", id=form_id)
    if form is None:
        return DetailContent()

    # Controls belong to the shell, not the message.
    for tag in form.find_all(["button", "script", "style"]):
        tag.decompose()

    links = [
        a["href"] for a in form.find_all("a", href=True)
        if a["href"].startswith("http")
    ]
    images = [
        img["src"] for img in form.find_all("img", src=True)
        if "javax.faces.resource" not in img["src"]
    ]
    return DetailContent(
        text=clean(form.get_text(" ")),
        body_html=str(form),
        links=list(dict.fromkeys(links)),
        images=list(dict.fromkeys(images)),
    )


def is_read(record: dict[str, str]) -> bool:
    return clean(record.get("Estado", "")).upper() == READ_STATUS
