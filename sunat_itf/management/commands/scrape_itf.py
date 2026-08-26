"""Scrape the SUNAT Consulta de ITF into the database."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from sunat_itf.models import ItfRecord
from sunat_itf.services import ItfPortalClient, ItfPortalError, ItfSynchronizer
from sunat_itf.services.parsing import current_period, previous_period
from sync.cli import add_sol_arguments, sol_credentials


class Command(BaseCommand):
    help = "Scrape the SUNAT ITF report (Consulta de ITF) into the database."

    def add_arguments(self, parser) -> None:
        add_sol_arguments(parser)
        parser.add_argument(
            "--period", default=None,
            help="End period yyyymm; the range runs from that year's January to it. "
                 "Defaults to the current month.",
        )
        parser.add_argument(
            "--previous-month", action="store_true",
            help="Use the just-closed month as the end period (what the cron does).",
        )
        parser.add_argument(
            "--headful", action="store_true",
            help="Show the browser window while logging in (for debugging).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        creds = sol_credentials(options)

        period_end = options["period"] or (
            previous_period() if options["previous_month"] else current_period()
        )

        client = ItfPortalClient(
            taxpayer_id=creds.ruc,
            username=creds.username,
            password=creds.password,
            headless=not options["headful"],
        )

        self.stdout.write(f"Authenticating {creds.ruc}/{creds.username} ...")
        try:
            client.login()
            self.stdout.write(self.style.SUCCESS("Login OK, ITF report reached"))
            result = ItfSynchronizer(client).run(period_end)
        except ItfPortalError as exc:
            raise CommandError(str(exc)) from exc

        stored = ItfRecord.objects.for_taxpayer(creds.ruc).count()
        self.stdout.write(self.style.SUCCESS(f"Done: {result} ({stored} stored in total)"))
