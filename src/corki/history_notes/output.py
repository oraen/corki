"""Bound readable recovery output without cutting JSON or opaque identities."""

import json


def size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def bounded_text(value: dict, key: str, budget: int) -> dict:
    """Retain metadata and the longest prefix whose entire JSON fits the budget."""
    if size(value) <= budget:
        return value
    text = value[key]
    result = {**value, key: "", "truncated": True}
    if size(result) > budget:
        raise ValueError("Tool output budget is too small for recovery metadata")
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if size({**result, key: text[:middle]}) <= budget:
            low = middle
        else:
            high = middle - 1
    return {**result, key: text[:low]}


def bounded_records(key: str, records, limit: int, budget: int) -> dict:
    """Preserve full identifiers, signal omitted records, and bound previews."""
    result = {key: [], "truncated": False}
    for record in records:
        if len(result[key]) >= limit:
            result["truncated"] = True
            break
        candidate = {key: [*result[key], record], "truncated": False}
        if size(candidate) > budget:
            result["truncated"] = True
            field = next((f for f in ("truncated_content", "text") if f in record), None)
            if field is not None:
                available = budget - size({key: [*result[key], {}], "truncated": True}) + 2
                try:
                    record = bounded_text(record, field, available)
                except ValueError:
                    break
                result[key].append(record)
            break
        result[key].append(record)
    if size(result) > budget:
        raise ValueError("Tool output budget is too small for recovery metadata")
    return result
