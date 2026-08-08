"""Parse pending invoice XML and regenerate the finance alerts."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from finance_analytics.services.alerts import rebuild_alerts
from finance_analytics.services.xml_extract import extract_pending


class Command(BaseCommand):
    help = "Extract UBL fields from pending comprobantes and rebuild alerts."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        stats = extract_pending(force=options["force"])
        self.stdout.write(
            f"XML — parseados: {stats['parsed']} · fallidos: {stats['failed']} · "
            f"sin cambios: {stats['skipped']}"
        )
        alerts = rebuild_alerts()
        self.stdout.write(
            f"Alertas — activas: {alerts['active']} · total: {alerts['total']}"
        )
