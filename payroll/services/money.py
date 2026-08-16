"""Decimal money helpers. Every persisted amount goes through ``money()``:
2 decimals, ROUND_HALF_UP (spec §0.6). Floats never enter the engine."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal | int | str) -> Decimal:
    """Round to cents, half up."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def D(value) -> Decimal:  # noqa: N802 — deliberate short constructor name
    """Build a Decimal from anything the DB or JSON hands back."""
    return value if isinstance(value, Decimal) else Decimal(str(value))
