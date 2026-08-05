"""Evaluate the alert rules over whatever is in the database (spec §7)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from sensor_sunat import rules
from sensor_sunat.models import Alert


class Command(BaseCommand):
    help = "Run every prototype rule (R1..R11) and create Alerts, without duplicates."

    def handle(self, *args: Any, **options: Any) -> None:
        created, already_open = rules.run_all()
        open_alerts = Alert.objects.filter(status="OPEN").count()
        self.stdout.write(self.style.SUCCESS(
            f"rules: {created} alerts created, {already_open} already open "
            f"({open_alerts} OPEN in total)"
        ))
        for alert in Alert.objects.filter(status="OPEN").order_by("severity", "rule"):
            self.stdout.write(f"  [{alert.severity}] {alert.rule}: {alert.title}")
