"""Scrape electronic comprobantes (all types, both directions) and their XML."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from sunat_cpe.models import ElectronicInvoice
from sunat_cpe.services import CpePortalClient, CpePortalError, CpeSynchronizer
from sunat_cpe.services.constants import ALL_TIPOS
from sunat_cpe.services.parsing import current_period, recent_periods
from sync.cli import add_sol_arguments, sol_credentials


class Command(BaseCommand):
    help = (
        "Scrape electronic comprobantes (facturas / NC / ND, emitidas y recibidas) "
        "and their signed XML. Daily window by default; --full walks the full history."
    )

    def add_arguments(self, parser) -> None:
        add_sol_arguments(parser)
        parser.add_argument(
            "--window", type=int, default=2,
            help="Daily mode: current month plus N earlier months (default 2).",
        )
        parser.add_argument(
            "--full", action="store_true",
            help="Backfill: walk backwards until the data runs out.",
        )
        parser.add_argument(
            "--start-period", default=None,
            help="Backfill start yyyymm (defaults to the current month).",
        )
        parser.add_argument("--floor-period", default="201401")
        parser.add_argument("--stop-after-empty", type=int, default=3)
        parser.add_argument(
            "--types", default=",".join(ALL_TIPOS),
            help="Comma-separated tipoConsulta values to sync (default all 10-16).",
        )
        parser.add_argument("--no-xml", action="store_true")
        parser.add_argument("--redownload", action="store_true")
        parser.add_argument("--headful", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        creds = sol_credentials(options)
        tipos = tuple(t.strip() for t in options["types"].split(",") if t.strip())

        client = CpePortalClient(
            taxpayer_id=creds.ruc,
            username=creds.username,
            password=creds.password,
            headless=not options["headful"],
        )
        self.stdout.write(f"Authenticating {creds.ruc}/{creds.username} ...")
        try:
            client.login()
            self.stdout.write(self.style.SUCCESS("Login OK, CPE consulta reached"))
            synchronizer = CpeSynchronizer(
                client, tipos=tipos,
                download_xml=not options["no_xml"], redownload=options["redownload"],
            )
            if options["full"]:
                result = synchronizer.backfill(
                    options["start_period"] or current_period(),
                    stop_after_empty=options["stop_after_empty"],
                    floor_period=options["floor_period"],
                )
            else:
                result = synchronizer.sync_periods(recent_periods(options["window"]))
        except CpePortalError as exc:
            raise CommandError(str(exc)) from exc

        stored = ElectronicInvoice.objects.for_account(creds.ruc).count()
        self.stdout.write(self.style.SUCCESS(f"Done: {result} ({stored} stored in total)"))
