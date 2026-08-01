"""RUC validation."""

from __future__ import annotations

from django.core.exceptions import ValidationError

# Weights SUNAT applies to the first ten digits of a RUC.
RUC_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
RUC_LENGTH = 11


def ruc_check_digit(ruc: str) -> int:
    """Return the modulus-11 check digit for the first ten digits of a RUC."""
    total = sum(int(digit) * weight for digit, weight in zip(ruc, RUC_WEIGHTS))
    remainder = 11 - (total % 11)
    return {10: 0, 11: 1}.get(remainder, remainder)


def is_valid_ruc(ruc: str) -> bool:
    if len(ruc) != RUC_LENGTH or not ruc.isdigit():
        return False
    return ruc_check_digit(ruc) == int(ruc[-1])


def validate_ruc(value: str) -> None:
    """Django validator: reject anything that is not a well-formed RUC.

    Checking the digit locally avoids spending a SUNAT request on a typo.
    """
    if not is_valid_ruc(value):
        raise ValidationError(
            "%(value)s is not a valid RUC (11 digits with a matching check digit).",
            params={"value": value},
        )
