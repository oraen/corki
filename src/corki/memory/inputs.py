"""Model-aware extraction budgeting and shared memory UTF-8 middle truncation."""

from corki.config.settings import CorkiSettings


def extraction_token_budget(settings: CorkiSettings) -> int:
    model = settings.resolved_memory_extraction_model
    info = settings.model_context_info(model)
    window = info.resolved_context_window
    explicit = settings.memories_extraction_token_limit
    if window is None:
        return explicit if explicit is not None else 150_000
    budget = max(1, (window * info.effective_context_window_percent // 100) * 70 // 100)
    return min(budget, explicit) if explicit is not None else budget


def truncate_rollout(text: str, token_limit: int) -> str:
    return truncate_memory_text(text, token_limit)


def truncate_memory_text(text: str, token_limit: int) -> str:
    """Keep half of the 4-byte/token budget at each end; marker is extra.

    Codex uses this for rollout, memory reads and summaries. It is not a tokenizer
    or a parseable-JSON guarantee. Decoding ignores only incomplete edge code points.
    """
    data = text.encode("utf-8")
    byte_limit = max(0, token_limit) * 4
    if len(data) <= byte_limit:
        return text
    left = byte_limit // 2
    right = byte_limit - left
    prefix = data[:left].decode("utf-8", errors="ignore")
    suffix = data[-right:].decode("utf-8", errors="ignore") if right else ""
    omitted = (len(data) - byte_limit + 3) // 4
    return f"{prefix}…{omitted} tokens truncated…{suffix}"
