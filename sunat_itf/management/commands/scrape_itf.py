"""Scrape the SUNAT Consulta de ITF into the database."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sunat_itf.models import ItfRecord
from sunat_itf.services import ItfPortalClient, ItfPortalError, ItfSynchronizer
from sunat_itf.services.parsing import current_period, previous_period


class Command(BaseCommand):
    help = "Scrape the SUNAT ITF report (Consulta de ITF) into the database."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--taxpayer-id", default=settings.SUNAT_RUC, help="RUC")
        parser.add_argument("--username", default=settings.SUNAT_USER)
        parser.add_argument("--password", default=settings.SUNAT_PASS)
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
        credentials = (
            options["taxpayer_id"], options["username"], options["password"]
        )
        if not all(credentials):
            raise CommandError(
                "Missing credentials. Set SUNAT_RUC, SUNAT_USER and SUNAT_PASS in .env "
                "or pass --taxpayer-id/--username/--password."
            )

        period_end = options["period"] or (
            previous_period() if options["previous_month"] else current_period()
        )

        client = ItfPortalClient(
            taxpayer_id=options["taxpayer_id"],
            username=options["username"],
            password=options["password"],
            headless=not options["headful"],
        )

        self.stdout.write(f"Authenticating {options['taxpayer_id']}/{options['username']} ...")
        try:
            client.login()
            self.stdout.write(self.style.SUCCESS("Login OK, ITF report reached"))
            result = ItfSynchronizer(client).run(period_end)
        except ItfPortalError as exc:
            raise CommandError(str(exc)) from exc

        stored = ItfRecord.objects.for_taxpayer(options["taxpayer_id"]).count()
        self.stdout.write(self.style.SUCCESS(f"Done: {result} ({stored} stored in total)"))
