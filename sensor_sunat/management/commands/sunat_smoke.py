"""P0 smoke test: token + RVIE periods printed to the console. No database."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sensor_sunat.sunat_client import SunatApiError, SunatClient


class Command(BaseCommand):
    help = "Smoke test: obtain a SIRE token and print the real RVIE periods."

    def handle(self, *args: Any, **options: Any) -> None:
        client = SunatClient()
        try:
            client.get_token()
            self.stdout.write(self.style.SUCCESS("Token OK"))
            ejercicios = client.fetch_periods(settings.SUNAT["COD_LIBRO_RVIE"])
        except SunatApiError as exc:
            raise CommandError(f"SUNAT error: {exc}\npayload={exc.payload}") from exc

        if not ejercicios:
            raise CommandError("SUNAT returned no periods — check the credentials.")

        self.stdout.write(f"{'Year':<6}{'Period':<10}{'State':<6}Description")
        for year in ejercicios:
            for period in year.get("lisPeriodos") or []:
                self.stdout.write(
                    f"{year.get('numEjercicio', ''):<6}"
                    f"{period.get('perTributario', ''):<10}"
                    f"{period.get('codEstado', ''):<6}"
                    f"{period.get('desEstado', '')}"
                )
        self.stdout.write(self.style.SUCCESS("P0 smoke passed: real periods listed."))
