"""Per-receipt refresh: the row's «Actualizar» button re-queries the
receipt's whole period (list + details) — matching by position would be
fragile, and the period query costs the same handful of POSTs."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="sunat_rhe.refresh_receipt",
    # A real browser logs in: slow, and never two at once per SOL user.
    time_limit=60 * 10,
    soft_time_limit=60 * 8,
)
def refresh_receipt(receipt_id: str) -> dict[str, Any]:
    from accounts.models import SunatCredential
    from financials.services import ingest

    from .models import FeeReceipt
    from .services import RhePortalClient, RheSynchronizer

    receipt = FeeReceipt.objects.filter(pk=receipt_id).first()
    if receipt is None or not receipt.period:
        return {"status": "desaparecido"}
    credential = SunatCredential.objects.filter(
        organization__ruc=receipt.account_ruc
    ).first()
    if credential is None:
        return {"status": "sin_credencial"}

    client = RhePortalClient(
        taxpayer_id=receipt.account_ruc,
        username=credential.sol_username,
        password=credential.password,
    )
    result = RheSynchronizer(client).sync_periods([receipt.period])
    ingest.ingest_fee_receipts(receipt.account_ruc)
    logger.info("RHE refresh %s: %s", receipt.full_number, result)
    return {"status": "ok", "period": receipt.period}
