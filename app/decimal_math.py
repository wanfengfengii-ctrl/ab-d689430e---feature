"""Exact decimal helpers.

All geometric arithmetic is performed on :class:`fractions.Fraction` so that
inch/mm conversion (25.4 = 127/5) and relative-move accumulation never hit
floating point binary error or Decimal context rounding.  Inputs and outputs
are canonical decimal strings.
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from typing import Any

# Canonical finite decimal: optional sign, integer/fraction digits (at least
# one digit somewhere), optional decimal exponent.  No underscores, hex,
# Infinity / NaN / sNaN.
CANONICAL_DECIMAL_RE = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z"
)


def parse_decimal_text(text: str) -> Decimal:
    """Parse a canonical decimal token or raise ``ValueError``."""
    if not isinstance(text, str) or not CANONICAL_DECIMAL_RE.fullmatch(text):
        raise ValueError(f"non-canonical decimal: {text!r}")
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError(f"non-finite decimal: {text!r}")
    return value


def coerce_decimal(value: Any, field: str) -> Decimal:
    """Coerce a JSON scalar (int/float/str) into a finite Decimal.

    Floats go through their shortest ``repr`` round-trip (``0.1`` stays
    ``0.1``); booleans are rejected explicitly.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a decimal number")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        dec = Decimal(str(value))
        if not dec.is_finite():
            raise ValueError(f"{field} must be finite")
        return dec
    if isinstance(value, str):
        try:
            return parse_decimal_text(value)
        except ValueError as exc:
            raise ValueError(f"{field}: {exc}") from None
    raise ValueError(f"{field} must be a decimal number")


# Inputs beyond this order of magnitude (millimetres) are rejected as
# non-canonical rather than converted into enormous bignums.
_EXPONENT_LIMIT = 1000


def decimal_to_fraction(value: Decimal) -> Fraction:
    """Exact conversion of a (possibly exponent-form) Decimal to Fraction."""
    sign, digits, exponent = value.as_tuple()
    if abs(exponent) > _EXPONENT_LIMIT:
        raise ValueError(f"decimal exponent out of range: {value}")
    numerator = 0
    for digit in digits:
        numerator = numerator * 10 + digit
    if sign:
        numerator = -numerator
    if exponent >= 0:
        return Fraction(numerator * 10**exponent, 1)
    return Fraction(numerator, 10 ** (-exponent))


def fraction_to_decimal(value: Fraction) -> Decimal:
    """Exact conversion back to a terminating Decimal.

    Every value produced by this audit has a denominator whose prime factors
    are only 2 and 5 (decimal inputs scaled by 254/10), so the decimal
    expansion always terminates.
    """
    denominator = value.denominator
    twos = 0
    fives = 0
    d = denominator
    while d % 2 == 0:
        twos += 1
        d //= 2
    while d % 5 == 0:
        fives += 1
        d //= 5
    if d != 1:  # pragma: no cover - defensive, cannot occur with decimal inputs
        raise ValueError(f"non-terminating decimal expansion: {value}")
    scale = max(twos, fives)
    scaled = value.numerator * (10**scale // denominator)
    return Decimal(scaled).scaleb(-scale)


def canonical_str(value: Fraction) -> str:
    """Canonical plain decimal string: no exponent, no redundant zeros."""
    dec = fraction_to_decimal(value)
    if dec == 0:
        return "0"
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
