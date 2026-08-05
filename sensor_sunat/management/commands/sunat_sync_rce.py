"""Sync one RCE period: proposal, boxes, inconsistencies, compliance, FV0621,
then rebuild the Supplier aggregates."""

from __future__ import annotations

import base64
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from sensor_sunat.models import (
    Book,
    BoxSnapshot,
    Inconsistency,
    PurchaseDoc,
    RawArtifact,
    SscoEntry,
    Supplier,
)
from sensor_sunat.parsers import decode_report_text, iter_boxes, iter_proposal_rows
from sensor_sunat.sunat_client import SunatApiError, SunatClient


class Command(BaseCommand):
    help = "Sync RCE (purchases) for one period and rebuild supplier aggregates."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--periodo", required=True, help="Tax period yyyymm, e.g. 202606")
        parser.add_argument(
            "--skip-fv0621", action="store_true",
            help="Skip the FV0621 query (documented as PUT in the manual).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        period = options["periodo"]
        book_code = settings.SUNAT["COD_LIBRO_RCE"]
        try:
            with SunatClient() as client:
                self._sync_proposal(client, period, book_code)
                self._sync_boxes(client, period)
                self._sync_inconsistencies(client, period, book_code)
                self._sync_compliance(client, period, book_code)
                if not options["skip_fv0621"]:
                    self._sync_fv0621(client, period)
        except SunatApiError as exc:
            raise CommandError(f"SUNAT error: {exc}\npayload={exc.payload}") from exc

        self._rebuild_suppliers()

    # ------------------------------------------------------------- proposal
    def _sync_proposal(self, client: SunatClient, period: str, book_code: str) -> None:
        self.stdout.write("RCE proposal: dispatching ticket ...")
        try:
            target_dir = client.fetch_ticket_result(
                lambda: client.dispatch_rce_proposal(period),
                book_code=book_code, period=period, endpoint_label="rce_proposal",
            )
        except SunatApiError as exc:
            if exc.status == 422:
                self.stdout.write(self.style.WARNING(f"proposal: {exc.payload}"))
                return
            raise
        RawArtifact.objects.create(
            endpoint="rce_proposal", params={"periodo": period},
            local_path=str(target_dir),
        )
        created = updated = 0
        for txt in sorted(Path(target_dir).glob("*.txt")):
            text = decode_report_text(txt.read_bytes())
            for row in iter_proposal_rows(text):
                _, was_created = PurchaseDoc.objects.update_or_create(
                    doc_type=row["doc_type"] or "",
                    series=row["series"] or "",
                    number=row["number"] or "",
                    defaults={
                        "tax_period": row["tax_period"] or period,
                        "issue_date": row["issue_date"],
                        "supplier_ruc": row["party_doc"] or "",
                        "supplier_name": row["party_name"] or "",
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
            f"proposal: {created} purchase docs created, {updated} updated"
        ))

    # ----------------------------------------------------------------- boxes
    def _sync_boxes(self, client: SunatClient, period: str) -> None:
        try:
            content = client.fetch_boxes_report(period)
        except SunatApiError as exc:
            self.stdout.write(self.style.WARNING(f"boxes: {exc.payload or exc}"))
            return
        path = client.save_artifact("rce_boxes", period, content, "casillas.txt")
        RawArtifact.objects.create(
            endpoint="rce_boxes", params={"periodo": period}, local_path=str(path)
        )
        count = 0
        for box, amount in iter_boxes(decode_report_text(content)):
            BoxSnapshot.objects.update_or_create(
                book=Book.RCE, tax_period=period, box=box, defaults={"amount": amount}
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"boxes: {count} box amounts stored"))

    # -------------------------------------------------------- inconsistencies
    def _sync_inconsistencies(self, client: SunatClient, period: str, book_code: str) -> None:
        try:
            target_dir = client.fetch_ticket_result(
                lambda: client.dispatch_rce_inconsistencies(period),
                book_code=book_code, period=period,
                endpoint_label="rce_inconsistencies",
            )
        except SunatApiError as exc:
            if exc.status == 422:
                self.stdout.write(self.style.WARNING(f"inconsistencies: {exc.payload}"))
                return
            raise
        RawArtifact.objects.create(
            endpoint="rce_inconsistencies", params={"periodo": period},
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
                    book=Book.RCE, tax_period=period,
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
            "rce_compliance", period, base64.b64decode(encoded), name
        )
        RawArtifact.objects.create(
            endpoint="rce_compliance", params={"periodo": period}, local_path=str(path)
        )
        self.stdout.write(self.style.SUCCESS(f"compliance: saved {name}"))

    # ---------------------------------------------------------------- FV0621
    def _sync_fv0621(self, client: SunatClient, period: str) -> None:
        try:
            payload = client.fetch_fv0621(period)
        except SunatApiError as exc:
            self.stdout.write(self.style.WARNING(f"FV0621: {exc.payload or exc}"))
            return
        RawArtifact.objects.create(
            endpoint="rce_fv0621", params={"periodo": period},
            local_path="(JSON stored in params)",
        )
        RawArtifact.objects.filter(
            endpoint="rce_fv0621", params__periodo=period
        ).update(params={"periodo": period, "payload": payload})
        self.stdout.write(self.style.SUCCESS(f"FV0621: {payload}"))

    # -------------------------------------------------------------- suppliers
    def _rebuild_suppliers(self) -> None:
        """Aggregate PurchaseDoc by RUC; igv_at_risk applies to SSCO members."""
        ssco_rucs = set(SscoEntry.objects.values_list("ruc", flat=True))
        rows = (
            PurchaseDoc.objects.exclude(supplier_ruc="")
            .values("supplier_ruc")
            .annotate(total=Sum("total"), igv=Sum("igv"))
        )
        for row in rows:
            ruc = row["supplier_ruc"]
            name = (
                PurchaseDoc.objects.filter(supplier_ruc=ruc)
                .exclude(supplier_name="")
                .values_list("supplier_name", flat=True).first() or ""
            )
            in_ssco = ruc in ssco_rucs
            Supplier.objects.update_or_create(
                ruc=ruc,
                defaults={
                    "business_name": name,
                    "in_ssco": in_ssco,
                    "total_purchased": row["total"] or Decimal("0"),
                    "igv_at_risk": (row["igv"] or Decimal("0")) if in_ssco else Decimal("0"),
                },
            )
        self.stdout.write(self.style.SUCCESS(f"suppliers: {rows.count()} aggregated"))
