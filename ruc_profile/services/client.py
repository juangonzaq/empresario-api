"""Fetches the full SUNAT RUC profile: the main table plus every button."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

from suppliers.services.constants import LOOKUP_URL
from suppliers.services.ruc_client import (
    RucLookupClient,
    RucLookupError,
    TaxpayerProfile,
)

from .constants import FORM_CONTEXT, FORM_MODE, SECTIONS, SectionSpec
from .parsers import SectionData, parse_section

logger = logging.getLogger(__name__)


@dataclass
class FullProfile:
    """The main RUC table plus one parsed :class:`SectionData` per button."""

    taxpayer: TaxpayerProfile
    sections: dict[str, SectionData] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def section(self, key: str) -> SectionData | None:
        return self.sections.get(key)


class RucProfileClient(RucLookupClient):
    """Extends the taxpayer lookup with the detail pages behind each button.

    The buttons are plain form posts to the same endpoint with a different
    ``accion``, so they reuse the session, retry policy and headers already set up
    by :class:`~suppliers.services.ruc_client.RucLookupClient`.
    """

    def fetch_section(
        self, ruc: str, spec: SectionSpec, business_name: str = ""
    ) -> SectionData:
        payload = {
            "accion": spec.action,
            "contexto": FORM_CONTEXT,
            "modo": FORM_MODE,
            "nroRuc": ruc,
            "desRuc": business_name,
        }
        try:
            response = self.session.post(LOOKUP_URL, data=payload, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RucLookupError(
                f"Section {spec.key} failed for {ruc}: {exc}"
            ) from exc
        return parse_section(spec, response.text)

    def fetch_full_profile(
        self, ruc: str, sections: tuple[SectionSpec, ...] = SECTIONS
    ) -> FullProfile:
        """Fetch the main table and every section.

        A section that fails is recorded in ``errors`` and the rest still run: one
        broken page must not cost the whole profile.
        """
        taxpayer = self.fetch(ruc)
        full = FullProfile(taxpayer=taxpayer)

        for spec in sections:
            try:
                full.sections[spec.key] = self.fetch_section(
                    ruc, spec, taxpayer.business_name
                )
            except RucLookupError as exc:
                logger.warning("Section %s failed for %s: %s", spec.key, ruc, exc)
                full.errors[spec.key] = str(exc)
        return full
