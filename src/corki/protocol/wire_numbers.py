"""Exact native JSON numbers and encoding, independent of Python's float range."""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def validate_number(value):
    if isinstance(value, WireNumber):
        return
    if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError("expected JSON number")


@dataclass(frozen=True, slots=True)
class WireNumber:
    token: str

    def __post_init__(self):
        if not isinstance(self.token, str) or _NUMBER.fullmatch(self.token) is None:
            raise ValueError("invalid JSON number")
        # serde_json's arbitrary_precision scanner canonicalizes exponent markers
        # and supplies a missing plus; fractional/exponent digits remain untouched.
        mantissa, exponent, digits = self.token.lower().partition("e")
        if exponent:
            sign = "" if digits.startswith(("+", "-")) else "+"
            object.__setattr__(self, "token", mantissa + "e" + sign + digits)

    def value(self):
        # 640 is Python's minimum configurable integer-string conversion limit.
        # Longer tokens never go through int/float, even for very large exponents.
        if len(self.token) > 640:
            return self
        if not any(char in self.token for char in ".e"):
            return int(self.token)
        value = float(self.token)
        if math.isfinite(value) and json.dumps(value) == self.token:
            return value
        return self


def loads_number_values(value):
    """Decode JSON numbers exactly without reinterpreting user object keys."""

    def number(token):
        return WireNumber(token).value()

    def invalid_constant(token):
        raise ValueError("invalid JSON constant")

    return json.loads(value, parse_int=number, parse_float=number, parse_constant=invalid_constant)


def dumps_wire(value, *, ensure_ascii=False, sort_keys=False, separators=(",", ":")):
    return "".join(
        iterencode_wire(
            value, ensure_ascii=ensure_ascii, sort_keys=sort_keys, separators=separators
        )
    )


def iterencode_wire(value, *, ensure_ascii=False, sort_keys=False, separators=(",", ":")):
    """Encode validated number tokens as numbers, never quoted text or markers.

    Normal strings use JSON escaping. Recursive containers are rejected; repeated
    non-recursive references are legal. No replacement of serialized substrings is
    used, so strings resembling numbers or private serde markers stay strings.
    """
    active = set()

    def encode(item):
        if isinstance(item, WireNumber):
            yield item.token
        elif isinstance(item, (Mapping, list, tuple)):
            identity = id(item)
            if identity in active:
                raise ValueError("circular JSON reference")
            active.add(identity)
            try:
                mapping = isinstance(item, Mapping)
                yield "{" if mapping else "["
                entries = item.items() if mapping else enumerate(item)
                if mapping and sort_keys:
                    entries = sorted(entries)
                for index, (key, child) in enumerate(entries):
                    if index:
                        yield separators[0]
                    if mapping:
                        if not isinstance(key, str):
                            raise TypeError("JSON object key must be a string")
                        yield json.dumps(key, ensure_ascii=ensure_ascii)
                        yield separators[1]
                    yield from encode(child)
                yield "}" if mapping else "]"
            finally:
                active.remove(identity)
        else:
            yield json.dumps(item, ensure_ascii=ensure_ascii, allow_nan=False)

    yield from encode(value)
