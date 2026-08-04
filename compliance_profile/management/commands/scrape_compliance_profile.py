"""Scrape the SUNAT compliance profile (perfil de cumplimiento) into the database."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from compliance_profile.models import ComplianceRating
from compliance_profile.services import (
    CompliancePortalClient,
    CompliancePortalError,
    ComplianceSynchronizer,
)


class Command(BaseCommand):
    help = "Scrape the SUNAT compliance profile (perfil de cumplimiento) into the database."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--taxpayer-id", default=settings.SUNAT_RUC, help="RUC")
        parser.add_argument("--username", default=settings.SUNAT_USER)
        parser.add_argument("--password", default=settings.SUNAT_PASS)
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
        credentials = (
            options["taxpayer_id"], options["username"], options["password"]
        )
        if not all(credentials):
            raise CommandError(
                "Missing credentials. Set SUNAT_RUC, SUNAT_USER and SUNAT_PASS in .env "
                "or pass --taxpayer-id/--username/--password."
            )

        client = CompliancePortalClient(
            taxpayer_id=options["taxpayer_id"],
            username=options["username"],
            password=options["password"],
            headless=not options["headful"],
        )

        self.stdout.write(f"Authenticating {options['taxpayer_id']}/{options['username']} ...")
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

        stored = ComplianceRating.objects.for_taxpayer(options["taxpayer_id"]).count()
        current = (
            ComplianceRating.objects.for_taxpayer(options["taxpayer_id"])
            .current().first()
        )
        style = self.style.WARNING if result.details_failed else self.style.SUCCESS
        self.stdout.write(style(f"Done: {result} ({stored} quarters stored)"))
        if current:
            self.stdout.write(self.style.SUCCESS(
                f"Current rating: {current.rating} ({current.get_rating_display()}) "
                f"for quarter {current.period}"
            ))
