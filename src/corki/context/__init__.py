"""Prompt assembly, project discovery, truncation, and future compaction."""

from corki.context.builder import ContextBuilder, ContextSnapshot
from corki.context.history import active_history, normalize_tool_pairs
from corki.context.tokens import estimate_item_tokens, estimate_request_tokens, estimate_text_tokens
from corki.context.truncation import truncate_text
from corki.context.window import ContextWindowManager, PreparedHistory

__all__ = [
    "ContextBuilder",
    "ContextSnapshot",
    "ContextWindowManager",
    "PreparedHistory",
    "active_history",
    "estimate_item_tokens",
    "estimate_request_tokens",
    "estimate_text_tokens",
    "normalize_tool_pairs",
    "truncate_text",
]
