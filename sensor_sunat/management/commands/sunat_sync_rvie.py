"""Sync one RVIE period: proposal + boxes + inconsistencies + compliance PDF."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sensor_sunat.models import Book, BoxSnapshot, Inconsistency, RawArtifact, SalesDoc
from sensor_sunat.parsers import (
    decode_report_text,
    iter_boxes,
    iter_proposal_rows,
)
from sensor_sunat.sunat_client import SunatApiError, SunatClient


class Command(BaseCommand):
    help = "Sync RVIE (sales) for one period: proposal, boxes, inconsistencies, compliance."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--periodo", required=True, help="Tax period yyyymm, e.g. 202606")

    def handle(self, *args: Any, **options: Any) -> None:
        period = options["periodo"]
        book_code = settings.SUNAT["COD_LIBRO_RVIE"]
        try:
            with SunatClient() as client:
                self._sync_proposal(client, period, book_code)
                self._sync_boxes(client, period)
                self._sync_inconsistencies(client, period, book_code)
                self._sync_compliance(client, period, book_code)
        except SunatApiError as exc:
            raise CommandError(f"SUNAT error: {exc}\npayload={exc.payload}") from exc

    # ------------------------------------------------------------- proposal
    def _sync_proposal(self, client: SunatClient, period: str, book_code: str) -> None:
        self.stdout.write("RVIE proposal: dispatching ticket ...")
        try:
            target_dir = client.fetch_ticket_result(
                lambda: client.dispatch_rvie_proposal(period),
                book_code=book_code, period=period, endpoint_label="rvie_proposal",
            )
        except SunatApiError as exc:
            if exc.status == 422:  # e.g. 1070: no data for the period
                self.stdout.write(self.style.WARNING(f"proposal: {exc.payload}"))
                return
            raise
        RawArtifact.objects.create(
            endpoint="rvie_proposal", params={"periodo": period},
            local_path=str(target_dir),
        )
        created = updated = 0
        for txt in sorted(Path(target_dir).glob("*.txt")):
            text = decode_report_text(txt.read_bytes())
            for row in iter_proposal_rows(text):
                _, was_created = SalesDoc.objects.update_or_create(
                    doc_type=row["doc_type"] or "",
                    series=row["series"] or "",
                    number=row["number"] or "",
                    defaults={
                        "tax_period": row["tax_period"] or period,
                        "issue_date": row["issue_date"],
                        "customer_ruc": row["party_doc"] or "",
                        "customer_name": row["party_name"] or "",
                        "base_amount": row["base_amount"],
                        "igv": row["igv"],
                        "total": row["total"],
                        "car_sunat": row["car_sunat"] or "",
                        "raw_extra": row["raw_extra"],
                    },
                )
                created += was_created
                updated += not was_created
        self.stdout.write(self.style.SUCCESS(
            f"proposal: {created} sales docs created, {updated} updated"
        ))

    # ----------------------------------------------------------------- boxes
    def _sync_boxes(self, client: SunatClient, period: str) -> None:
        try:
            content = client.fetch_boxes_report(period)
        except SunatApiError as exc:
            self.stdout.write(self.style.WARNING(f"boxes: {exc.payload or exc}"))
            return
        path = client.save_artifact("rvie_boxes", period, content, "casillas.txt")
        RawArtifact.objects.create(
            endpoint="rvie_boxes", params={"periodo": period}, local_path=str(path)
        )
        count = 0
        for box, amount in iter_boxes(decode_report_text(content)):
            BoxSnapshot.objects.update_or_create(
                book=Book.RVIE, tax_period=period, box=box,
                defaults={"amount": amount},
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"boxes: {count} box amounts stored"))

    # -------------------------------------------------------- inconsistencies
    def _sync_inconsistencies(self, client: SunatClient, period: str, book_code: str) -> None:
        try:
            target_dir = client.fetch_ticket_result(
                lambda: client.dispatch_rvie_inconsistencies(period),
                book_code=book_code, period=period,
                endpoint_label="rvie_inconsistencies",
            )
        except SunatApiError as exc:
            if exc.status == 422:
                self.stdout.write(self.style.WARNING(f"inconsistencies: {exc.payload}"))
                return
            raise
        RawArtifact.objects.create(
            endpoint="rvie_inconsistencies", params={"periodo": period},
            local_path=str(target_dir),
        )
        count = 0
        for txt in sorted(Path(target_dir).glob("*.txt")):
            text = decode_report_text(txt.read_bytes())
            lines = [line for line in text.splitlines() if line.strip()]
            for line in lines[1:]:
                cells = [cell.strip() for cell in line.split("|")]
                if len(cells) < 2:
                    continue
                _, was_created = Inconsistency.objects.get_or_create(
                    book=Book.RVIE, tax_period=period,
                    kind=cells[0][:60] or "inconsistency",
                    detail={"cells": cells},
                )
                count += was_created
        self.stdout.write(self.style.SUCCESS(f"inconsistencies: {count} new stored"))

    # ------------------------------------------------------------ compliance
    def _sync_compliance(self, client: SunatClient, period: str, book_code: str) -> None:
        try:
            payload = client.fetch_compliance_report(period, book_code)
        except SunatApiError as exc:
            self.stdout.write(self.style.WARNING(f"compliance: {exc.payload or exc}"))
            return
        encoded = payload.get("archivoPdf")
        if not encoded:
            self.stdout.write(self.style.WARNING("compliance: no PDF in response"))
            return
        name = payload.get("nombreArchivoPdf") or f"cumplimiento_{period}.pdf"
        path = client.save_artifact(
            "rvie_compliance", period, base64.b64decode(encoded), name
        )
        RawArtifact.objects.create(
            endpoint="rvie_compliance", params={"periodo": period}, local_path=str(path)
        )
        self.stdout.write(self.style.SUCCESS(f"compliance: saved {name}"))
