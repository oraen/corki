"""Exact stage-one source ordering across ISO and legacy UTC timestamps."""

from datetime import UTC, datetime


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def source_version_epoch_micros(value: str) -> int | None:
    """SQLite ordering key without julianday's millisecond rounding."""
    try:
        delta = _instant(value) - datetime(1970, 1, 1, tzinfo=UTC)
    except (TypeError, ValueError, OverflowError):
        return None
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def source_version_at_least(candidate: str, stored: str) -> bool:
    try:
        current = _instant(candidate)
    except (TypeError, ValueError):
        # A malformed candidate is never proof that a source was consumed.
        return False
    try:
        return current >= _instant(stored)
    except (TypeError, ValueError):
        # A valid candidate may replace an unorderable legacy row.
        return True


def source_version_newer(candidate: str, stored: str) -> bool:
    try:
        current = _instant(candidate)
    except (TypeError, ValueError):
        return False
    try:
        return current > _instant(stored)
    except (TypeError, ValueError):
        return True
