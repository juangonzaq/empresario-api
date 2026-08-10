"""Analyze mailbox messages with the LLM and rebuild the cases."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sunat_intel.services import analyzer, cases


class Command(BaseCommand):
    help = (
        "Run the AI analysis over mailbox messages that need it and rebuild "
        "the business cases. Cached results are skipped unless --force."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--force", action="store_true")
        parser.add_argument(
            "--ruc", default=settings.SUNAT_RUC,
            help="Empresa sobre la que trabajar. Por defecto, SUNAT_RUC del entorno.",
        )
        parser.add_argument(
            "--cases-only", action="store_true",
            help="Skip the LLM analysis and only regroup existing results.",
        )

    def handle(self, *args, **options):
        ruc = options["ruc"]
        if not ruc:
            raise CommandError(
                "Indica la empresa con --ruc (o define SUNAT_RUC en el entorno)."
            )
        if not options["cases_only"]:
            stats = analyzer.analyze_pending(
                taxpayer_id=ruc, limit=options["limit"], force=options["force"]
            )
            self.stdout.write(
                f"Analizados: {stats['analyzed']} · fallidos: {stats['failed']}"
            )
        case_stats = cases.rebuild_cases(ruc)
        self.stdout.write(
            f"Casos — creados: {case_stats['created']} · actualizados: "
            f"{case_stats['updated']} · eliminados: {case_stats['deleted']}"
        )
