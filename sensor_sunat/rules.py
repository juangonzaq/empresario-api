"""Alert rules engine — pure functions over the ORM (spec §7).

Each rule yields dicts with the Alert fields; ``run_all`` persists them without
duplicating: one OPEN alert per (rule, object key). R5, R10 and R12 are out of
the prototype's scope.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Iterator

from django.db.models import Sum
from django.utils import timezone

from .models import Alert, Inconsistency, Period, PurchaseDoc, SalesDoc, Supplier

logger = logging.getLogger(__name__)

# Update every January.
UIT_2026 = Decimal("5500")
RUS_LIMIT = UIT_2026 * 300          # 1,650,000 — regime threshold (R8)
PRICING_LIMIT = UIT_2026 * 1700     # 9,350,000 — transfer-pricing threshold (R9)
R2_AMOUNT_THRESHOLD = Decimal("5000")
R6_REVIEW_DAYS = 7
R7_CONCENTRATION = Decimal("0.30")

PENDING_WORDS = ("pendiente", "omiso", "no generado", "por generar")


def rule_r1_pending_periods() -> Iterator[dict[str, Any]]:
    """R1: a period whose SUNAT status suggests it is pending/omitted → RED."""
    for period in Period.objects.all():
        status = period.status.lower()
        if any(word in status for word in PENDING_WORDS):
            yield {
                "rule": "R1",
                "severity": Alert.Severity.RED,
                "title": f"{period.book} {period.tax_period} looks pending: {period.status}",
                "detail": {"book": period.book, "tax_period": period.tax_period,
                           "status": period.status},
                "key": f"{period.book}:{period.tax_period}",
            }


def rule_r2_open_inconsistencies() -> Iterator[dict[str, Any]]:
    """R2: unresolved inconsistency in the latest stored period → AMBER,
    RED when its detail carries an amount above 5,000."""
    latest = (
        Inconsistency.objects.order_by("-tax_period")
        .values_list("tax_period", flat=True).first()
    )
    if not latest:
        return
    for item in Inconsistency.objects.filter(tax_period=latest, resolved=False):
        amount = _first_amount(item.detail)
        severity = (
            Alert.Severity.RED
            if amount and amount > R2_AMOUNT_THRESHOLD
            else Alert.Severity.AMBER
        )
        yield {
            "rule": "R2",
            "severity": severity,
            "title": f"Unresolved inconsistency {item.kind} in {item.book} {item.tax_period}",
            "detail": item.detail,
            "amount_at_risk": amount,
            "key": str(item.pk),
        }


def rule_r3_ssco_suppliers() -> Iterator[dict[str, Any]]:
    """R3: supplier on the SSCO blacklist → RED, IGV at risk attached."""
    for supplier in Supplier.objects.filter(in_ssco=True):
        yield {
            "rule": "R3",
            "severity": Alert.Severity.RED,
            "title": f"Supplier {supplier.ruc} {supplier.business_name} is on the SSCO list",
            "detail": {"ruc": supplier.ruc, "total_purchased": str(supplier.total_purchased)},
            "amount_at_risk": supplier.igv_at_risk,
            "key": supplier.ruc,
        }


def rule_r4_registry_flags() -> Iterator[dict[str, Any]]:
    """R4: supplier NO HABIDO or not ACTIVO in the padrón → AMBER."""
    for supplier in Supplier.objects.exclude(registry_status="").exclude(
        registry_condition=""
    ):
        bad_condition = supplier.registry_condition.upper().startswith("NO HABIDO")
        bad_status = supplier.registry_status.upper() != "ACTIVO"
        if bad_condition or bad_status:
            yield {
                "rule": "R4",
                "severity": Alert.Severity.AMBER,
                "title": (
                    f"Supplier {supplier.ruc} is "
                    f"{supplier.registry_status}/{supplier.registry_condition}"
                ),
                "detail": {"ruc": supplier.ruc,
                           "status": supplier.registry_status,
                           "condition": supplier.registry_condition},
                "key": supplier.ruc,
            }


def rule_r6_unreviewed_purchases() -> Iterator[dict[str, Any]]:
    """R6: purchases unreviewed for more than 7 days → one AMBER summary."""
    cutoff = timezone.now() - timedelta(days=R6_REVIEW_DAYS)
    stale = PurchaseDoc.objects.filter(recognized__isnull=True, first_seen__lt=cutoff)
    count = stale.count()
    if count:
        total = stale.aggregate(total=Sum("total"))["total"] or Decimal("0")
        yield {
            "rule": "R6",
            "severity": Alert.Severity.AMBER,
            "title": f"{count} purchase documents pending review (older than {R6_REVIEW_DAYS} days)",
            "detail": {"count": count, "total": str(total)},
            "amount_at_risk": total,
            "key": "unreviewed",
        }


def rule_r7_customer_concentration() -> Iterator[dict[str, Any]]:
    """R7: one customer above 30% of the quarter's sales → INFO."""
    latest = (
        SalesDoc.objects.order_by("-tax_period")
        .values_list("tax_period", flat=True).first()
    )
    if not latest:
        return
    year, month = int(latest[:4]), int(latest[4:6])
    first_month = ((month - 1) // 3) * 3 + 1  # calendar quarter of the latest period
    periods = [f"{year:04d}{m:02d}" for m in range(first_month, first_month + 3)]
    docs = SalesDoc.objects.filter(tax_period__in=periods)
    grand_total = docs.aggregate(total=Sum("total"))["total"] or Decimal("0")
    if not grand_total:
        return
    by_customer = (
        docs.exclude(customer_ruc="").values("customer_ruc", "customer_name")
        .annotate(total=Sum("total")).order_by("-total")
    )
    for row in by_customer:
        share = (row["total"] or Decimal("0")) / grand_total
        if share > R7_CONCENTRATION:
            yield {
                "rule": "R7",
                "severity": Alert.Severity.INFO,
                "title": (
                    f"Customer {row['customer_ruc']} concentrates "
                    f"{share:.0%} of quarter sales"
                ),
                "detail": {"ruc": row["customer_ruc"], "name": row["customer_name"],
                           "share": f"{share:.2%}", "periods": periods},
                "key": row["customer_ruc"],
            }


def _yearly_sales_total() -> Decimal:
    year = str(timezone.now().year)
    return (
        SalesDoc.objects.filter(tax_period__startswith=year)
        .aggregate(total=Sum("total"))["total"] or Decimal("0")
    )


def rule_r8_regime_threshold() -> Iterator[dict[str, Any]]:
    """R8: yearly sales at ≥85% of 300 UIT → AMBER."""
    total = _yearly_sales_total()
    if total >= RUS_LIMIT * Decimal("0.85"):
        yield {
            "rule": "R8",
            "severity": Alert.Severity.AMBER,
            "title": f"Yearly sales {total:,.0f} ≥ 85% of the 300-UIT threshold",
            "detail": {"total": str(total), "threshold": str(RUS_LIMIT)},
            "amount_at_risk": total,
            "key": "r8",
        }


def rule_r9_pricing_threshold() -> Iterator[dict[str, Any]]:
    """R9: yearly sales at ≥80% of 1,700 UIT → AMBER."""
    total = _yearly_sales_total()
    if total >= PRICING_LIMIT * Decimal("0.80"):
        yield {
            "rule": "R9",
            "severity": Alert.Severity.AMBER,
            "title": f"Yearly sales {total:,.0f} ≥ 80% of the 1,700-UIT threshold",
            "detail": {"total": str(total), "threshold": str(PRICING_LIMIT)},
            "amount_at_risk": total,
            "key": "r9",
        }


def rule_r11_missing_receipt() -> Iterator[dict[str, Any]]:
    """R11: an already-closed period whose status never reached 'generado' →
    RED ('did your accountant register the book?').

    The prototype has no receipt artifact yet, so period status is the proxy.
    """
    current = timezone.now().strftime("%Y%m")
    for period in Period.objects.exclude(tax_period=current):
        status = period.status.lower()
        if status in {"?", ""}:
            continue
        if "generado" not in status and "cerrado" not in status:
            yield {
                "rule": "R11",
                "severity": Alert.Severity.RED,
                "title": (
                    f"{period.book} {period.tax_period} has no generation receipt "
                    f"(status: {period.status}) — did your accountant register it?"
                ),
                "detail": {"book": period.book, "tax_period": period.tax_period,
                           "status": period.status},
                "key": f"{period.book}:{period.tax_period}",
            }


ALL_RULES = (
    rule_r1_pending_periods,
    rule_r2_open_inconsistencies,
    rule_r3_ssco_suppliers,
    rule_r4_registry_flags,
    rule_r6_unreviewed_purchases,
    rule_r7_customer_concentration,
    rule_r8_regime_threshold,
    rule_r9_pricing_threshold,
    rule_r11_missing_receipt,
)


def run_all() -> tuple[int, int]:
    """Evaluate every rule; returns (created, already_open)."""
    created = skipped = 0
    for rule in ALL_RULES:
        for finding in rule():
            key = finding.pop("key", finding["title"])
            exists = Alert.objects.filter(
                rule=finding["rule"], status="OPEN", detail__contains={"_key": key}
            ).exists()
            if exists:
                skipped += 1
                continue
            finding["detail"] = {**finding.get("detail", {}), "_key": key}
            Alert.objects.create(**finding)
            created += 1
    logger.info("rules: %d alerts created, %d already open", created, skipped)
    return created, skipped


def _first_amount(detail: dict[str, Any]) -> Decimal | None:
    for value in detail.values():
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            try:
                return Decimal(value.replace(",", ""))
            except Exception:
                continue
    return None
