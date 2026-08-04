"""Scrape the SUNAFIL casilla electrónica."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sunafil.models import ItemKind, SunafilItem
from sunafil.services import SunafilClient, SunafilLoginError, SunafilSynchronizer
from sunafil.services.constants import LISTINGS, LISTINGS_BY_KIND


class Command(BaseCommand):
    help = "Scrape SUNAFIL's casilla electrónica (orientations, requirements, notices)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--taxpayer-id", default=settings.SUNAT_RUC, help="RUC")
        parser.add_argument("--username", default=settings.SUNAT_USER)
        parser.add_argument("--password", default=settings.SUNAT_PASS)
        parser.add_argument(
            "--kind", action="append", dest="kinds",
            choices=[spec.kind for spec in LISTINGS],
            help="Scrape only these listings. Repeatable.",
        )
        parser.add_argument(
            "--no-details", action="store_true",
            help="Do not open orientation bodies (opening marks them as read).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        credentials = (
            options["taxpayer_id"], options["username"], options["password"]
        )
        if not all(credentials):
            raise CommandError(
                "Missing credentials. SUNAFIL employer access uses Clave SOL: set "
                "SUNAT_RUC, SUNAT_USER and SUNAT_PASS in .env."
            )

        client = SunafilClient(
            taxpayer_id=options["taxpayer_id"],
            username=options["username"],
            password=options["password"],
        )
        self.stdout.write(f"Authenticating {options['taxpayer_id']} on SUNAFIL ...")
        try:
            client.login()
        except SunafilLoginError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Login OK"))

        listings = (
            tuple(LISTINGS_BY_KIND[kind] for kind in options["kinds"])
            if options["kinds"] else LISTINGS
        )
        synchronizer = SunafilSynchronizer(
            client, fetch_details=not options["no_details"]
        )
        result = synchronizer.run(listings=listings)

        style = self.style.WARNING if result.failed else self.style.SUCCESS
        self.stdout.write(style(f"Done: {result}"))

        stored = SunafilItem.objects.filter(taxpayer_id=options["taxpayer_id"])
        for kind, label in ItemKind.choices:
            count = stored.filter(kind=kind).count()
            unread = stored.filter(kind=kind, is_read=False).count()
            if count:
                self.stdout.write(f"  {label[:42]:42} {count:4}  ({unread} sin leer)")

        pending = stored.actionable().unread()
        if pending.exists():
            self.stdout.write(self.style.WARNING("\nObligaciones sin leer:"))
            for item in pending.order_by("due_date")[:10]:
                self.stdout.write(
                    f"  {item.record_number[:38]:38} vence {item.due_date or '?'}"
                )
