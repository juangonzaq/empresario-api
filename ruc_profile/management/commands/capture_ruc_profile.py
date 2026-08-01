"""Capture the full SUNAT RUC profile: main table plus every button."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from ruc_profile.models import RucSnapshot
from ruc_profile.services import RucProfileSynchronizer
from ruc_profile.services.sync import DEFAULT_MAX_AGE_DAYS
from ruc_profile.tasks import profiled_rucs
from suppliers.services.ruc_client import RucLookupError

RISK_LABELS = (
    ("has_coactive_debt", "deuda coactiva"),
    ("has_tax_omissions", "omisiones tributarias"),
    ("has_probatory_acts", "actas probatorias"),
    ("reactiva_peru_debt", "Reactiva Perú"),
    ("covid_guarantee_debt", "garantías COVID-19"),
)


class Command(BaseCommand):
    help = "Capture the full SUNAT profile for one or more RUCs."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--ruc", action="append", dest="rucs",
            help="Capture only these RUCs. Repeatable. "
                 "Defaults to the company RUC plus every tracked supplier.",
        )
        parser.add_argument(
            "--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
            help=f"Reuse a snapshot newer than this (default {DEFAULT_MAX_AGE_DAYS}).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Capture again even if a recent snapshot exists.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        rucs = options["rucs"] or profiled_rucs()
        if not rucs:
            raise CommandError(
                "No RUCs to capture. Register suppliers or set SUNAT_RUC in .env."
            )

        self.stdout.write(f"Capturing {len(rucs)} RUC profile(s) from SUNAT ...")
        try:
            result = RucProfileSynchronizer().run(
                rucs, max_age_days=None if options["force"] else options["max_age_days"]
            )
        except RucLookupError as exc:
            raise CommandError(str(exc)) from exc

        style = self.style.WARNING if result.failed else self.style.SUCCESS
        self.stdout.write(style(f"Done: {result}"))

        for ruc in rucs:
            snapshot = (
                RucSnapshot.objects.filter(ruc=ruc, succeeded=True)
                .order_by("-captured_on").first()
            )
            if snapshot is None:
                self.stdout.write(f"  {ruc}  no snapshot")
                continue
            flags = [label for field, label in RISK_LABELS if getattr(snapshot, field)]
            summary = ", ".join(flags) if flags else "sin alertas"
            # A declared headcount of 0 is data, not a missing value.
            workers = "-" if snapshot.worker_count is None else snapshot.worker_count
            self.stdout.write(
                f"  {ruc}  {snapshot.status}/{snapshot.condition}  "
                f"trabajadores={workers}  {summary}"
            )
            if snapshot.changed:
                self.stdout.write(self.style.WARNING(f"      cambios: {snapshot.change_summary}"))
