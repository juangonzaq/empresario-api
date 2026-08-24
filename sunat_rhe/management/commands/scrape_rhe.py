"""Developer utility: run the fee-receipt scraper outside the sync flow.

End users never touch this — the product path is the «Traer honorarios»
button and the nightly run. This exists for debugging a scrape with the
browser visible::

    python manage.py scrape_rhe --ruc 20604442533 --headful
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from accounts.models import SunatCredential
from sunat_cpe.services.parsing import recent_periods

from ...services import RhePortalClient, RhePortalError, RheSynchronizer


class Command(BaseCommand):
    help = "Scrape los recibos por honorarios recibidos (uso de desarrollo)."

    def add_arguments(self, parser):
        parser.add_argument("--ruc", required=True, help="RUC de la empresa.")
        parser.add_argument(
            "--window", type=int, default=2,
            help="Meses hacia atrás a consultar (default 2).",
        )
        parser.add_argument(
            "--headful", action="store_true",
            help="Abre el navegador visible para depurar.",
        )

    def handle(self, *args, **options):
        credential = SunatCredential.objects.filter(
            organization__ruc=options["ruc"]
        ).first()
        if credential is None:
            raise CommandError(f"No hay clave SOL guardada para {options['ruc']}.")

        client = RhePortalClient(
            taxpayer_id=options["ruc"],
            username=credential.sol_username,
            password=credential.password,
            headless=not options["headful"],
        )
        try:
            result = RheSynchronizer(client).sync_periods(
                recent_periods(options["window"])
            )
        except RhePortalError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(str(result)))
