"""Server retry advice, measured from receipt rather than from a later retry."""

import math
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def retry_after_deadline(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.isascii() and value.isdecimal():
        if len(value) > 20:
            return None
        delay = int(value)
    else:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            delay = max(0.0, (date - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(delay) or delay > 1_000_000_000:
        return None
    return time.monotonic() + delay


def retry_after_header(headers: Mapping) -> float | None:
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "retry-after":
            return retry_after_deadline(value)
    return None
