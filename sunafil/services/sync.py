"""Records SUNAFIL casilla listings into the database."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from ..models import ItemKind, SunafilItem
from .client import ListingPage, SunafilClient, SunafilError
from .constants import LISTINGS, ListingSpec
from .parsers import (
    clean,
    identity_key,
    is_read,
    parse_date,
    parse_datetime,
    parse_int,
    parse_orientation_detail,
)

logger = logging.getLogger(__name__)


@dataclass
class SunafilSyncResult:
    created: int = 0
    updated: int = 0
    details_fetched: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"{self.created} new, {self.updated} updated, "
            f"{self.details_fetched} details fetched, {self.failed} failed"
        )


class SunafilSynchronizer:
    """Pulls the casilla listings and, optionally, the orientation bodies.

    Details are only ever fetched for orientations. Opening a requirement or a
    notification registers an *acuse de recibo* on SUNAFIL and starts the legal
    response deadline, so that is never done automatically.
    """

    def __init__(self, client: SunafilClient, *, fetch_details: bool = True):
        self.client = client
        self.fetch_details = fetch_details

    def run(
        self,
        listings: tuple[ListingSpec, ...] = LISTINGS,
        on_date: date | None = None,
    ) -> SunafilSyncResult:
        today = on_date or timezone.localdate()
        result = SunafilSyncResult()

        for spec in listings:
            try:
                page = self.client.fetch_listing(spec)
            except SunafilError as exc:
                logger.warning("Listing %s failed: %s", spec.kind, exc)
                result.failed += 1
                continue

            items = self._save_listing(spec, page, today, result)
            if self.fetch_details and spec.detail_is_safe:
                self._fetch_details(spec, page, items, result)
        return result

    def _save_listing(
        self, spec: ListingSpec, page: ListingPage, today: date,
        result: SunafilSyncResult,
    ) -> list[tuple[int, SunafilItem]]:
        saved: list[tuple[int, SunafilItem]] = []

        for index, record in enumerate(page.records()):
            try:
                item, created = self._save_row(spec, record, today)
            except Exception:
                result.failed += 1
                logger.exception("Failed to save %s row %s", spec.kind, index)
                continue
            result.created += created
            result.updated += not created
            saved.append((index, item))
        return saved

    @transaction.atomic
    def _save_row(
        self, spec: ListingSpec, record: dict[str, str], today: date
    ) -> tuple[SunafilItem, bool]:
        key = identity_key(spec, record)
        defaults = {**self._map_fields(record), "last_seen_on": today}

        item, created = SunafilItem.objects.get_or_create(
            taxpayer_id=self.client.taxpayer_id,
            kind=spec.kind,
            external_key=key,
            defaults={**defaults, "first_seen_on": today},
        )
        if not created:
            for field, value in defaults.items():
                setattr(item, field, value)
            item.save()
        return item, created

    def _map_fields(self, record: dict[str, str]) -> dict[str, Any]:
        get = lambda name: clean(record.get(name, ""))  # noqa: E731

        subject = get("Asunto") or get("Tipo de Requerimiento")
        identifier = (
            get("Registro") or get("Orden de Inspección") or get("Expediente Sancionador")
        )
        acknowledged_at = self._aware(parse_datetime(get("Fecha Acuse de Recibo")))
        return {
            "subject": subject,
            "category": get("Categoría"),
            "record_number": identifier[:120],
            "office": get("Intendencia")[:120],
            "status": get("Estado")[:60],
            # Orientations report LEÍDO in a column; requirements and notifications
            # have no such column and instead carry an acuse de recibo date, which is
            # the equivalent signal. Without this they all look permanently unread.
            "is_read": is_read(record) or acknowledged_at is not None,
            "deposited_at": self._aware(parse_datetime(get("Fecha de Depósito"))),
            "acknowledged_at": acknowledged_at,
            "notified_on": parse_date(get("Fecha de Notificación")),
            "due_date": parse_date(get("Fecha Límite de Presentación")),
            "deadline_days": parse_int(get("Plazo")),
            "row": record,
        }

    @staticmethod
    def _aware(value):
        if value and timezone.is_naive(value):
            return timezone.make_aware(value)
        return value

    def _fetch_details(
        self, spec: ListingSpec, page: ListingPage,
        items: list[tuple[int, SunafilItem]], result: SunafilSyncResult,
    ) -> None:
        """Open the orientations whose body has not been stored yet.

        Each post consumes the page's ViewState, so the listing is re-read between
        details to pick up a fresh one.
        """
        view_state = page.view_state
        button_ids = page.detail_button_ids

        for index, item in items:
            if item.detail_fetched_at or index >= len(button_ids):
                continue
            button_id = button_ids[index]
            if not button_id:
                continue
            try:
                detail_page = self.client.fetch_orientation_detail(
                    spec, button_id, view_state
                )
            except SunafilError as exc:
                logger.warning("Detail for %s failed: %s", item.subject[:40], exc)
                result.failed += 1
                continue

            content = parse_orientation_detail(detail_page, spec.detail_form_id)
            item.detail_text = content.text
            item.detail_html = content.body_html
            item.detail_links = content.links
            item.detail_images = content.images
            item.detail_fetched_at = timezone.now()
            # Opening it is what marks it read on SUNAFIL's side.
            item.is_read = True
            item.status = "LEÍDO"
            item.save()
            result.details_fetched += 1

            fresh = self.client.fetch_listing(spec)
            view_state = fresh.view_state
            button_ids = fresh.detail_button_ids
