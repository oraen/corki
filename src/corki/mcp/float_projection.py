"""RMCP binary32 rounding and shortest JSON projection without a native runtime.

Serde casts integer Content directly to f32; a prior Python float conversion can
double-round u64. Decimal output uses the nearest shortest round-tripping value,
ties to even, and zmij 1.0.19's f32 fixed/scientific notation thresholds.
"""

import math
import struct
from fractions import Fraction
from functools import lru_cache

from corki.protocol.wire_numbers import WireNumber


def _bits(value: int | float) -> int:
    if type(value) is int:
        if not -(2**63) <= value < 2**64:
            raise ValueError("integer outside RMCP buffered range")
        magnitude = abs(value)
        shift = max(0, magnitude.bit_length() - 24)
        divisor = 1 << shift
        quotient, remainder = divmod(magnitude, divisor)
        if 2 * remainder > divisor or (2 * remainder == divisor and quotient % 2):
            quotient += 1
        # This integer has at most 24 significant bits, so the subsequent f64
        # conversion is exact, including carry into the next binary exponent.
        value = (quotient << shift) * (-1 if value < 0 else 1)
    elif type(value) is not float or not math.isfinite(value):
        raise ValueError("expected finite RMCP number")
    try:
        return int.from_bytes(struct.pack(">f", value), "big")
    except OverflowError:
        raise ValueError("number outside finite f32 range") from None


def _ratio(bits: int) -> Fraction:
    exponent, significand = bits >> 23, bits & 0x7FFFFF
    if exponent:
        significand |= 1 << 23
    power = max(1, exponent) - 150
    return Fraction(significand << power) if power >= 0 else Fraction(significand, 1 << -power)


def _decimal(coefficient: int, power: int) -> str:
    digits = str(coefficient).rstrip("0")
    power += len(str(coefficient)) - len(digits)
    exponent = len(digits) + power - 1
    if not -6 <= exponent <= 12:
        mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        return f"{mantissa}e{exponent:+d}"
    point = len(digits) + power
    if point <= 0:
        return "0." + "0" * -point + digits
    if point >= len(digits):
        return digits + "0" * (point - len(digits)) + ".0"
    return digits[:point] + "." + digits[point:]


@lru_cache(maxsize=256)
def _shortest(bits: int) -> str:
    sign = "-" if bits >> 31 else ""
    bits &= 0x7FFFFFFF
    if bits == 0:
        return sign + "0.0"
    if bits >= 0x7F800000:
        raise ValueError("expected finite f32 bits")
    value = _ratio(bits)
    lower, upper = (value + _ratio(bits - 1)) / 2, (value + _ratio(bits + 1)) / 2
    inclusive = bits % 2 == 0
    exponent = len(str(value.numerator)) - len(str(value.denominator))
    scale = Fraction(10) ** exponent
    if value < scale:
        exponent -= 1
        scale /= 10
    # IEEE binary32 needs at most nine significant decimal digits. Use exact
    # rounding intervals rather than parsing through f64 at midpoint boundaries.
    for digits in range(1, 10):
        quotient = value // scale
        candidates = sorted((quotient, quotient + 1), key=lambda q: (abs(q * scale - value), q % 2))
        for coefficient in candidates:
            candidate = coefficient * scale
            if lower < candidate < upper or (inclusive and candidate in (lower, upper)):
                return sign + _decimal(coefficient, exponent - digits + 1)
        scale /= 10
    raise AssertionError("finite f32 has no nine-digit round trip")


def project_f32(value: int | float | WireNumber) -> int | float | WireNumber:
    """Project a typed number; callers must reject raw buffered decimal maps."""
    if isinstance(value, WireNumber):
        # A public/cold-restored Value may need an exact token (e.g. 1e+13).
        # Limit parsing to canonical finite f32 output, not arbitrary raw JSON.
        if len(value.token) > 32:
            raise ValueError("not a projected f32")
        token = _shortest(_bits(float(value.token)))
        if token != value.token:
            raise ValueError("not a projected f32")
    else:
        token = _shortest(_bits(value))
    return WireNumber(token).value()
