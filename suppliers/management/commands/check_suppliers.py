"""Check every tracked supplier's standing on SUNAT. Meant to run daily."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from suppliers.models import Supplier
from suppliers.services import SupplierMonitor


class Command(BaseCommand):
    help = "Look up each tracked supplier on SUNAT and record today's standing."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--ruc", action="append", dest="rucs",
            help="Check only these RUCs. Repeatable.",
        )
        parser.add_argument(
            "--all", action="store_true",
            help="Include suppliers marked as untracked.",
        )
        parser.add_argument(
            "--skip-checked", action="store_true",
            help="Skip suppliers already checked today (safe to retry a failed run).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        queryset = Supplier.objects.all()
        if not options["all"]:
            queryset = queryset.tracked()
        if options["rucs"]:
            queryset = queryset.filter(ruc__in=options["rucs"])
            missing = set(options["rucs"]) - set(queryset.values_list("ruc", flat=True))
            if missing:
                raise CommandError(f"Not registered as suppliers: {', '.join(sorted(missing))}")

        total = queryset.count()
        if not total:
            self.stdout.write(self.style.WARNING("No suppliers to check."))
            return

        self.stdout.write(f"Checking {total} supplier(s) on SUNAT ...")
        result = SupplierMonitor().run(
            suppliers=queryset, skip_checked_today=options["skip_checked"]
        )

        style = self.style.WARNING if result.failed else self.style.SUCCESS
        self.stdout.write(style(f"Done: {result}"))

        flagged = queryset.model.objects.filter(pk__in=queryset).with_issues()
        if flagged.exists():
            self.stdout.write(self.style.WARNING("\nSuppliers needing attention:"))
            for supplier in flagged:
                self.stdout.write(
                    f"  {supplier.ruc}  {supplier.display_name[:45]:<45} "
                    f"{supplier.status} / {supplier.condition}"
                )
