"""Write to disk the XML that is already in the database but not in the
document archive.

Comprobantes scraped before the archive existed hold their XML only in
``xml_content``. The sync writes the copy for every row it touches again
(the recent months); this command covers the whole history at once::

    python manage.py archive_xml                    # every company
    python manage.py archive_xml --ruc 20604442533  # one company

Re-running is safe: only rows without a file are written.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from sunat_cpe.models import ElectronicInvoice
from sunat_cpe.services.sync import archive_xml


class Command(BaseCommand):
    help = "Escribe en la carpeta de comprobantes los XML que solo están en la base."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--ruc", default=None, help="Solo esta empresa.")

    def handle(self, *args: Any, **options: Any) -> None:
        queryset = ElectronicInvoice.objects.exclude(xml_content="").filter(xml_file="")
        if options["ruc"]:
            queryset = queryset.for_account(options["ruc"])
        written = 0
        for invoice in queryset.order_by("pk").iterator(chunk_size=200):
            archive_xml(invoice)
            invoice.save(update_fields=["xml_file", "updated_at"])
            written += 1
        self.stdout.write(self.style.SUCCESS(f"{written} XML escritos en disco"))
