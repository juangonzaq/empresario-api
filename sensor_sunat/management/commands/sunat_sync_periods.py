"""Sync RVIE + RCE period lists into the Period model (endpoints 1 and 2)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sensor_sunat.models import Book, Period
from sensor_sunat.sunat_client import SunatApiError, SunatClient


class Command(BaseCommand):
    help = "Upsert the RVIE and RCE periods SUNAT reports for the taxpayer."

    def handle(self, *args: Any, **options: Any) -> None:
        client = SunatClient()
        books = (
            (Book.RVIE, settings.SUNAT["COD_LIBRO_RVIE"]),
            (Book.RCE, settings.SUNAT["COD_LIBRO_RCE"]),
        )
        created = updated = 0
        for book, code in books:
            try:
                ejercicios = client.fetch_periods(code)
            except SunatApiError as exc:
                raise CommandError(f"{book}: SUNAT error: {exc}\npayload={exc.payload}") from exc
            for year in ejercicios:
                for row in year.get("lisPeriodos") or []:
                    _, was_created = Period.objects.update_or_create(
                        book=book,
                        tax_period=row.get("perTributario", ""),
                        defaults={
                            "status": row.get("desEstado") or "?",
                            "status_code": row.get("codEstado") or "",
                        },
                    )
                    created += was_created
                    updated += not was_created
            self.stdout.write(f"{book}: synced")
        self.stdout.write(self.style.SUCCESS(f"Periods: {created} created, {updated} updated"))
