"""Maps CPE records onto ElectronicInvoice rows.

Two entry points:

* :meth:`sync_periods` — re-query an explicit list of periods across the chosen
  tipoConsulta values and upsert. This is the daily job: it simply picks up
  newly issued documents (a nota de crédito that offsets a factura shows up as
  its own record referencing the original, so balances are cross-referenced, not
  re-validated per document).
* :meth:`backfill` — walk periods backwards until every tipoConsulta comes back
  empty for enough consecutive months (the initial full load).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.utils import timezone

from core import archive

from ..models import ElectronicInvoice
from .client import CpePortalClient
from .constants import ALL_TIPOS
from .parsing import previous_period, record_fields

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    xml_downloaded: int = 0
    xml_failed: int = 0
    periods_scanned: int = 0
    oldest_period: str = ""

    def __str__(self) -> str:
        return (
            f"{self.created} created, {self.updated} updated; "
            f"XML {self.xml_downloaded} downloaded, {self.xml_failed} failed"
            + (f"; oldest {self.oldest_period}" if self.oldest_period else "")
        )


class CpeSynchronizer:
    def __init__(
        self,
        client: CpePortalClient,
        *,
        tipos: tuple[str, ...] = ALL_TIPOS,
        download_xml: bool = True,
        redownload: bool = False,
    ):
        self.client = client
        self.tipos = tipos
        self.download_xml = download_xml
        self.redownload = redownload

    # -------------------------------------------------------------- daily
    def sync_periods(self, periods: list[str]) -> SyncResult:
        """Query an explicit set of periods across every chosen tipo and upsert."""
        result = SyncResult()
        for period in periods:
            result.periods_scanned += 1
            for tipo in self.tipos:
                for record in self.client.query(period, tipo):
                    self._upsert(record, tipo, result)
        return result

    # ------------------------------------------------------------ backfill
    def backfill(
        self, start_period: str, *, stop_after_empty: int = 3, floor_period: str = "201401"
    ) -> SyncResult:
        """Walk backwards until every tipo is empty for N consecutive months."""
        result = SyncResult()
        period = start_period
        empty_streak = 0
        while period >= floor_period and empty_streak < stop_after_empty:
            result.periods_scanned += 1
            had_data = False
            for tipo in self.tipos:
                records = self.client.query(period, tipo)
                had_data = had_data or bool(records)
                for record in records:
                    self._upsert(record, tipo, result)
            if had_data:
                empty_streak = 0
                result.oldest_period = period
            else:
                empty_streak += 1
            logger.info(
                "CPE backfill %s: data=%s (empty streak %d/%d)",
                period, had_data, empty_streak, stop_after_empty,
            )
            period = previous_period(period)
        return result

    # -------------------------------------------------------------- upsert
    def _upsert(self, record: dict, tipo: str, result: SyncResult) -> None:
        fields = record_fields(record, self.client.taxpayer_id, tipo)
        if not fields["series"] or not fields["number"]:
            return
        invoice, created = ElectronicInvoice.objects.update_or_create(
            account_ruc=fields["account_ruc"],
            issuer_ruc=fields["issuer_ruc"],
            document_type=fields["document_type"],
            series=fields["series"],
            number=fields["number"],
            defaults={**fields, "last_seen_at": timezone.now()},
        )
        result.created += created
        result.updated += not created

        if self.download_xml and fields["can_download"]:
            self._maybe_download(invoice, fields, result)

    def _maybe_download(
        self, invoice: ElectronicInvoice, fields: dict, result: SyncResult
    ) -> None:
        if invoice.xml_content and not self.redownload:
            # The XML is already stored; only rows older than the document
            # archive (or whose file went missing) still need the copy on disk.
            if not invoice.xml_file:
                archive_xml(invoice)
                invoice.save(update_fields=["xml_file", "updated_at"])
            return
        try:
            filename, xml_text = self.client.download_xml(fields)
        except Exception:
            result.xml_failed += 1
            logger.exception("XML download failed for %s", fields["full_number"])
            return
        invoice.xml_content = xml_text
        invoice.xml_filename = filename
        invoice.xml_sha256 = hashlib.sha256(xml_text.encode("utf-8")).hexdigest()
        invoice.xml_downloaded_at = timezone.now()
        archive_xml(invoice)
        invoice.save(update_fields=[
            "xml_content", "xml_filename", "xml_sha256", "xml_downloaded_at",
            "xml_file", "updated_at",
        ])
        result.xml_downloaded += 1


def archive_xml(invoice: ElectronicInvoice) -> None:
    """Write the XML the row already holds in ``xml_content`` to the document
    archive (does not save the row).

    The text was decoded from SUNAT's bytes as ISO-8859-1, which maps every
    byte to exactly one character, so encoding it back yields the original
    file byte for byte, whatever encoding the XML itself declares.
    """
    archive.store(
        invoice.xml_file, invoice.xml_content.encode("ISO-8859-1", "replace"), "xml"
    )
