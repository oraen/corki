"""Deterministic tool-output truncation."""

from __future__ import annotations


def truncate_text(text: str, limit: int) -> str:
    """Keep useful head and tail evidence inside ``limit`` characters."""

    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker = f"\n... {len(text) - limit} characters omitted ...\n"
    if len(marker) >= limit:
        return text[: max(0, limit - 3)] + "..."[:limit]
    usable = max(0, limit - len(marker))
    head = usable * 3 // 5
    tail = usable - head
    return f"{text[:head]}{marker}{text[-tail:] if tail else ''}"
