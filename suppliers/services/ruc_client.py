"""Client for SUNAT's public RUC lookup (``e-consultaruc``).

This is public information: no SOL credentials are involved. The endpoint returns
HTML, so the profile is scraped out of the results page.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup, NavigableString
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .constants import (
    ACTION_BY_RUC,
    CONDITION_FOUND,
    FIELD_LABELS,
    INVALID_RUC_MARKER,
    LOOKUP_URL,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    STATUS_ACTIVE,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

RUC_NAME_PATTERN = re.compile(r"(\d{11})\s*-\s*(.+)", re.S)
QUOTES_PATTERN = re.compile(r"[\"']")


class RucLookupError(RuntimeError):
    """The lookup could not be completed."""


class RucNotFoundError(RucLookupError):
    """SUNAT has no taxpayer registered under this RUC."""


@dataclass
class TaxpayerProfile:
    """A taxpayer profile as published by SUNAT."""

    ruc: str
    business_name: str = ""
    taxpayer_type: str = ""
    trade_name: str = ""
    status: str = ""
    condition: str = ""
    fiscal_address: str = ""
    economic_activities: str = ""
    electronic_invoicing: str = ""
    registries: str = ""
    registered_on: date | None = None
    started_activities_on: date | None = None

    @property
    def is_active(self) -> bool:
        return self.status.upper() == STATUS_ACTIVE

    @property
    def is_found(self) -> bool:
        """``HABIDO``: SUNAT located the taxpayer at its declared address."""
        return self.condition.upper() == CONDITION_FOUND

    @property
    def has_issue(self) -> bool:
        """True when the taxpayer is not simply ACTIVO + HABIDO.

        Any unrecognised value counts as an issue so that new SUNAT states are
        surfaced rather than silently treated as healthy.
        """
        return not (self.is_active and self.is_found)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["registered_on"] = self.registered_on.isoformat() if self.registered_on else None
        data["started_activities_on"] = (
            self.started_activities_on.isoformat() if self.started_activities_on else None
        )
        return data


def _clean(text: str) -> str:
    return " ".join(text.split()).strip()


def _cell_text(cell) -> str:
    """Text of a cell, ignoring any table nested inside it.

    SUNAT repeats the whole results table inside the last cell of the outer one.
    The nested copy carries a few extra labels, so it must still be parsed as its
    own rows — it just must not bleed into the outer cell's value.
    """
    own_table = cell.find_parent("table")
    parts = [
        str(node)
        for node in cell.descendants
        # `type(...) is` rather than isinstance: Comment and Doctype subclass
        # NavigableString, and SUNAT leaves developer comments in the markup.
        if type(node) is NavigableString and node.find_parent("table") is own_table
    ]
    return _clean(" ".join(parts))


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


@dataclass
class RucLookupClient:
    """Looks up taxpayer profiles on SUNAT's public RUC service."""

    timeout: int = REQUEST_TIMEOUT
    session: requests.Session = field(default_factory=requests.Session, init=False)

    def __post_init__(self) -> None:
        self.session.headers.update({"User-Agent": USER_AGENT})
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def fetch(self, ruc: str) -> TaxpayerProfile:
        """Return the profile for ``ruc``.

        Raises :class:`RucNotFoundError` when SUNAT has no such taxpayer and
        :class:`RucLookupError` when the request itself fails.
        """
        html = self._request(ruc)
        return self._parse(html, ruc)

    def _request(self, ruc: str) -> str:
        payload = {
            "accion": ACTION_BY_RUC,
            # The endpoint requires a token but never validates it; a fresh random
            # value per request mirrors what the real form does.
            "token": uuid.uuid4().hex,
            "nroRuc": ruc,
        }
        try:
            response = self.session.post(
                LOOKUP_URL, data=payload, timeout=self.timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RucLookupError(f"SUNAT lookup failed for {ruc}: {exc}") from exc
        return response.text

    def _parse(self, html: str, ruc: str) -> TaxpayerProfile:
        soup = BeautifulSoup(html, "html.parser")
        rows = self._label_value_pairs(soup)

        if not rows:
            text = _clean(soup.get_text(" "))
            if INVALID_RUC_MARKER in text:
                raise RucNotFoundError(f"SUNAT rejected {ruc} as an invalid RUC.")
            raise RucNotFoundError(f"No taxpayer is registered under RUC {ruc}.")

        profile = TaxpayerProfile(ruc=ruc)
        for label, value in rows.items():
            if label.startswith("RUC"):
                profile.business_name = self._business_name(value)
                continue
            attribute = FIELD_LABELS.get(label)
            if not attribute:
                continue
            if attribute in ("registered_on", "started_activities_on"):
                setattr(profile, attribute, _parse_date(value))
            else:
                setattr(profile, attribute, value)

        if not profile.status:
            raise RucNotFoundError(f"SUNAT returned no status for RUC {ruc}.")
        return profile

    def _label_value_pairs(self, soup: BeautifulSoup) -> dict[str, str]:
        """Collect ``label: value`` cells from the results page.

        The page nests the same table more than once, so later duplicates are
        ignored and only the first occurrence of each label is kept.
        """
        pairs: dict[str, str] = {}
        for row in soup.find_all("tr"):
            cells = [_cell_text(cell) for cell in row.find_all("td")]
            # Rows alternate label/value and sometimes carry two pairs.
            for index in range(0, len(cells) - 1, 2):
                label, value = cells[index], cells[index + 1]
                if not label.endswith(":"):
                    continue
                label = label.rstrip(":").strip()
                if label and label not in pairs:
                    pairs[label] = value
        return pairs

    def _business_name(self, value: str) -> str:
        match = RUC_NAME_PATTERN.search(value)
        name = match.group(2) if match else value
        return _clean(QUOTES_PATTERN.sub(" ", name))
