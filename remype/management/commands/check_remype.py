"""Look RUCs up in the REMYPE registry."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from remype.models import RemypeCheck
from remype.services import RemypeLookupError, RemypeSynchronizer
from remype.services.sync import DEFAULT_MAX_AGE_DAYS
from remype.tasks import monitored_rucs


class Command(BaseCommand):
    help = "Check REMYPE accreditation for the company and/or its suppliers."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--ruc", action="append", dest="rucs",
            help="Check only these RUCs. Repeatable. Defaults to every "
                 "registered company plus every tracked supplier.",
        )
        parser.add_argument(
            "--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
            help=f"Reuse a stored check newer than this (default {DEFAULT_MAX_AGE_DAYS}).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Query REMYPE again even if a recent check exists.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        rucs = options["rucs"] or monitored_rucs()
        if not rucs:
            raise CommandError(
                "No RUCs to check. Register a company or track suppliers first."
            )

        self.stdout.write(f"Checking {len(rucs)} RUC(s) in REMYPE ...")
        try:
            result = RemypeSynchronizer().run(
                rucs, max_age_days=None if options["force"] else options["max_age_days"]
            )
        except RemypeLookupError as exc:
            raise CommandError(str(exc)) from exc

        style = self.style.WARNING if result.failed else self.style.SUCCESS
        self.stdout.write(style(f"Done: {result}"))

        for ruc in rucs:
            check = (
                RemypeCheck.objects.filter(ruc=ruc, succeeded=True)
                .order_by("-checked_on").first()
            )
            if check is None:
                self.stdout.write(f"  {ruc}  no data")
            elif check.is_registered:
                self.stdout.write(
                    f"  {ruc}  {check.condition or 'registered'} "
                    f"(since {check.accredited_on or '?'})"
                )
            else:
                self.stdout.write(f"  {ruc}  not registered in REMYPE")
