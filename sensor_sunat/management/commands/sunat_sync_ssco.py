"""Refresh the SSCO blacklist (public page, no token) and flag suppliers."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from sensor_sunat.models import PurchaseDoc, SscoEntry, Supplier

SSCO_URL = (
    "https://www.sunat.gob.pe/padronesnotificaciones/"
    "sujeSinCapacidadOperativa.html"
)
RUC_RE = re.compile(r"\b(1[05]\d{9}|20\d{9})\b")


class Command(BaseCommand):
    help = "Update SscoEntry from SUNAT's public SSCO page and flag suppliers."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            response = requests.get(SSCO_URL, timeout=60)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CommandError(f"Could not fetch the SSCO page: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        entries = self._parse_tables(soup)
        if not entries:
            entries = self._parse_linked_files(soup)
        if not entries:
            raise CommandError(
                "No RUCs found on the SSCO page or its linked files. "
                "SUNAT may have changed the page layout — inspect it manually."
            )

        created = 0
        for ruc, name, detail in entries:
            _, was_created = SscoEntry.objects.update_or_create(
                ruc=ruc, defaults={"business_name": name[:200], "detail": detail}
            )
            created += was_created
        self.stdout.write(self.style.SUCCESS(
            f"SSCO: {len(entries)} entries ({created} new)"
        ))

        flagged = 0
        ssco_rucs = set(SscoEntry.objects.values_list("ruc", flat=True))
        for supplier in Supplier.objects.all():
            in_ssco = supplier.ruc in ssco_rucs
            if in_ssco != supplier.in_ssco:
                supplier.in_ssco = in_ssco
                supplier.igv_at_risk = (
                    PurchaseDoc.objects.filter(supplier_ruc=supplier.ruc)
                    .aggregate(igv=Sum("igv"))["igv"] or Decimal("0")
                ) if in_ssco else Decimal("0")
                supplier.save(update_fields=["in_ssco", "igv_at_risk"])
                flagged += in_ssco
        self.stdout.write(self.style.SUCCESS(f"suppliers flagged in SSCO: {flagged}"))

    def _parse_tables(self, soup: BeautifulSoup) -> list[tuple[str, str, dict]]:
        entries = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
                ruc = next((c for c in cells if RUC_RE.fullmatch(c)), None)
                if not ruc:
                    continue
                name = next((c for c in cells if c != ruc and not c.isdigit()), "")
                entries.append((ruc, name, {"cells": cells, "source": "html-table"}))
        return entries

    def _parse_linked_files(self, soup: BeautifulSoup) -> list[tuple[str, str, dict]]:
        """Fallback: download any linked csv/txt/xls file and regex the RUCs."""
        entries: list[tuple[str, str, dict]] = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if not re.search(r"\.(csv|txt|xlsx?)($|\?)", href, re.I):
                continue
            url = urljoin(SSCO_URL, href)
            try:
                blob = requests.get(url, timeout=120).content
            except requests.RequestException:
                continue
            text = blob.decode("latin-1", errors="replace")
            for match in dict.fromkeys(RUC_RE.findall(text)):
                entries.append((match, "", {"source": url}))
            if entries:
                break
        return entries
