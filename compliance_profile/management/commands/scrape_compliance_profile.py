"""Scrape the SUNAT compliance profile (perfil de cumplimiento) into the database."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from compliance_profile.models import ComplianceRating
from compliance_profile.services import (
    CompliancePortalClient,
    CompliancePortalError,
    ComplianceSynchronizer,
)
from sync.cli import add_sol_arguments, sol_credentials


class Command(BaseCommand):
    help = "Scrape the SUNAT compliance profile (perfil de cumplimiento) into the database."

    def add_arguments(self, parser) -> None:
        add_sol_arguments(parser)
        parser.add_argument(
            "--skip-details", action="store_true",
            help="Only sync the quarterly headers, not each quarter's variables.",
        )
        parser.add_argument(
            "--refetch-details", action="store_true",
            help="Re-fetch the variables of quarters that already have them.",
        )
        parser.add_argument(
            "--headful", action="store_true",
            help="Show the browser window while logging in (for debugging).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        creds = sol_credentials(options)

        client = CompliancePortalClient(
            taxpayer_id=creds.ruc,
            username=creds.username,
            password=creds.password,
            headless=not options["headful"],
        )

        self.stdout.write(f"Authenticating {creds.ruc}/{creds.username} ...")
        try:
            client.login()
        except CompliancePortalError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Login OK, token captured"))

        synchronizer = ComplianceSynchronizer(
            client,
            fetch_details=not options["skip_details"],
            refetch_details=options["refetch_details"],
        )
        result = synchronizer.run()

        stored = ComplianceRating.objects.for_taxpayer(creds.ruc).count()
        current = (
            ComplianceRating.objects.for_taxpayer(creds.ruc)
            .current().first()
        )
        style = self.style.WARNING if result.details_failed else self.style.SUCCESS
        self.stdout.write(style(f"Done: {result} ({stored} quarters stored)"))
        if current:
            self.stdout.write(self.style.SUCCESS(
                f"Current rating: {current.rating} ({current.get_rating_display()}) "
                f"for quarter {current.period}"
            ))
