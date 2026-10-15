"""Codex-style millisecond exponential backoff with ten-percent jitter."""

import random


def retry_limit(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("model retry limits must be non-negative integers")
    return min(value, 100)


def backoff(base_seconds: float, retries_used: int) -> float:
    base_ms = min(int(min(base_seconds, (2**64 - 1) / 1000) * 1000), 2**64 - 1)
    raw_ms = min(base_ms * 2 ** min(retries_used, 100), 2**64 - 1)
    return min(int(raw_ms * random.uniform(0.9, 1.1)), 2**64 - 1) / 1000
