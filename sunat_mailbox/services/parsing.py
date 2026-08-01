"""Helpers that turn SUNAT's loosely-typed JSON fields into Python values."""

from __future__ import annotations

import html
import json
from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.utils import timezone

DATE_FORMATS = ("%d/%m/%Y",)
DATETIME_FORMATS = (
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)


def _parse(value: Any, formats: tuple[str, ...]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_date(value: Any) -> date | None:
    parsed = _parse(value, DATE_FORMATS)
    return parsed.date() if parsed else None


def parse_datetime(value: Any) -> datetime | None:
    """Parse a SUNAT timestamp, making it timezone-aware when ``USE_TZ`` is on."""
    parsed = _parse(value, DATETIME_FORMATS)
    if parsed and settings.USE_TZ and timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def clean_text(value: Any) -> str:
    """SUNAT double-encodes HTML entities in subjects (e.g. ``confirmaci&oacute;n``)."""
    return html.unescape(value).strip() if isinstance(value, str) else ""


def parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def system_id_from_detail(detail: dict[str, Any] | None) -> str:
    """Read the ``sistema`` id the download endpoint expects.

    It lives inside ``msjMensaje``, which is a JSON string for notifications but raw
    HTML for plain messages; ``"0"`` is the value SUNAT's own viewer falls back to.
    """
    raw = (detail or {}).get("msjMensaje")
    if not isinstance(raw, str):
        return "0"
    try:
        return str(json.loads(raw).get("sistema", "0"))
    except (json.JSONDecodeError, AttributeError):
        return "0"
