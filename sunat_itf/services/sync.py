"""Maps the Consulta de ITF HTML onto ItfRecord rows."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction

from ..models import ItfRecord
from .client import ItfPortalClient
from .parsing import iter_records, ytd_range

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    stored: int = 0
    periods: tuple[str, str] = ("", "")

    def __str__(self) -> str:
        return (
            f"{self.stored} ITF records stored for "
            f"{self.periods[0]}–{self.periods[1]}"
        )


class ItfSynchronizer:
    """Pulls the year-to-date ITF report and replaces the local snapshot.

    The report is a full snapshot of the requested range, and a single ITF row
    (same bank, period, operation code, amount) can legitimately repeat, so there
    is no stable per-row key to upsert on. The whole range is therefore replaced
    wholesale for the taxpayer on each run, which keeps re-runs idempotent.
    """

    def __init__(self, client: ItfPortalClient):
        self.client = client

    def run(self, period_end: str) -> SyncResult:
        return self.run_range(*ytd_range(period_end))

    def run_range(self, period_start: str, period_end: str) -> SyncResult:
        """Un rango dentro de UN ejercicio (el formulario del portal consulta
        por ejercicio; el borrado por periodos asume mismo año)."""
        html = self.client.fetch_report(period_start, period_end)
        records = list(iter_records(html, self.client.taxpayer_id))

        periods_in_range = {
            f"{y}{m:02d}"
            for y in [int(period_start[:4])]
            for m in range(int(period_start[4:]), int(period_end[4:]) + 1)
        }

        with transaction.atomic():
            deleted, _ = (
                ItfRecord.objects.for_taxpayer(self.client.taxpayer_id)
                .filter(period__in=periods_in_range)
                .delete()
            )
            ItfRecord.objects.bulk_create(
                ItfRecord(**fields) for fields in records
            )

        logger.info(
            "ITF %s–%s: %d rows stored (%d replaced)",
            period_start, period_end, len(records), deleted,
        )
        return SyncResult(stored=len(records), periods=(period_start, period_end))

    # El ITF existe desde 2004: caminar más atrás solo gasta sesión.
    FLOOR_YEAR = 2004

    def backfill(self, period_end: str, stop_after_empty_years: int = 2) -> SyncResult:
        """El histórico completo: el año en curso (hasta ``period_end``) y
        luego año por año hacia atrás — el reporte acepta rangos, así que
        cada ejercicio cuesta UNA sola consulta al portal. Se detiene tras
        ``stop_after_empty_years`` ejercicios seguidos sin movimientos, la
        misma heurística que el backfill de comprobantes."""
        result = self.run(period_end)
        total = result.stored
        earliest = result.periods[0]

        empty_years = 0
        year = int(period_end[:4]) - 1
        while empty_years < stop_after_empty_years and year >= self.FLOOR_YEAR:
            yearly = self.run_range(f"{year}01", f"{year}12")
            total += yearly.stored
            empty_years = 0 if yearly.stored else empty_years + 1
            if yearly.stored:
                earliest = f"{year}01"
            year -= 1
        return SyncResult(stored=total, periods=(earliest, period_end))
