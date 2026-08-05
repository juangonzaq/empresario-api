"""Update registry status/condition for known suppliers from the padrón reducido.

The daily ZIP holds ~11M rows; it is streamed and filtered to only the RUCs
present in Supplier plus the company's own RUC — nothing else touches the DB.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sensor_sunat.models import Supplier

PADRON_PAGE = "https://www.sunat.gob.pe/descargaPRR/mrc137_padron_reducido.html"


class Command(BaseCommand):
    help = "Stream the daily padrón reducido ZIP and update supplier registry flags."

    def handle(self, *args: Any, **options: Any) -> None:
        wanted = set(Supplier.objects.values_list("ruc", flat=True))
        wanted.add(settings.SUNAT["RUC"])
        if not wanted:
            self.stdout.write("No suppliers stored yet; nothing to check.")
            return

        zip_url = self._find_zip_url()
        self.stdout.write(f"Downloading {zip_url} (streaming) ...")
        with tempfile.NamedTemporaryFile(
            suffix=".zip", dir=settings.MEDIA_SUNAT_DIR, delete=False
        ) as handle:
            temp_path = Path(handle.name)
            try:
                with requests.get(zip_url, stream=True, timeout=300) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        handle.write(chunk)
            except requests.RequestException as exc:
                temp_path.unlink(missing_ok=True)
                raise CommandError(f"Padrón download failed: {exc}") from exc

        found = 0
        try:
            with zipfile.ZipFile(temp_path) as archive:
                name = archive.namelist()[0]
                with archive.open(name) as stream:
                    for raw_line in stream:
                        line = raw_line.decode("latin-1", errors="replace")
                        ruc = line[:11]
                        if ruc not in wanted:
                            continue
                        found += self._apply(ruc, line)
        finally:
            temp_path.unlink(missing_ok=True)

        self.stdout.write(self.style.SUCCESS(
            f"padrón: {found}/{len(wanted)} RUCs found and updated"
        ))

    def _find_zip_url(self) -> str:
        try:
            response = requests.get(PADRON_PAGE, timeout=60)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CommandError(f"Could not fetch the padrón page: {exc}") from exc
        soup = BeautifulSoup(response.text, "html.parser")
        for link in soup.find_all("a", href=True):
            if re.search(r"padron.*\.zip$", link["href"], re.I):
                return urljoin(PADRON_PAGE, link["href"])
        raise CommandError("No padrón ZIP link found on the download page.")

    def _apply(self, ruc: str, line: str) -> int:
        # Pipe-delimited: RUC|NOMBRE|ESTADO|CONDICION DE DOMICILIO|...
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 4:
            return 0
        name, status, condition = cells[1][:200], cells[2][:30], cells[3][:30]
        if ruc == settings.SUNAT["RUC"]:
            self.stdout.write(f"own RUC {ruc}: {status} / {condition}")
        updated = Supplier.objects.filter(ruc=ruc).update(
            registry_status=status, registry_condition=condition
        )
        if updated and not Supplier.objects.filter(ruc=ruc, business_name="").exists():
            pass  # name already set from the proposal; padrón name not needed
        elif updated:
            Supplier.objects.filter(ruc=ruc, business_name="").update(business_name=name)
        return 1
