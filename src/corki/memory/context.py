"""Prompt contribution for the compact long-term memory routing index."""

from __future__ import annotations

from pathlib import Path

from corki.context.tokens import estimate_text_tokens
from corki.prompting import PromptContribution, PromptRole, PromptSlot


class MemoryContextContributor:
    """Inject only the compact index; detailed memory stays progressively loaded."""

    def __init__(self, root: Path, *, enabled: bool, token_limit: int) -> None:
        self._root = root
        self._enabled = enabled
        self._token_limit = token_limit

    def contributions(self, *, cwd: Path, user_input: str, realtime_active: bool):
        del cwd, user_input, realtime_active
        if not self._enabled:
            return ()
        try:
            summary = (self._root / "memory_summary.md").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError, UnicodeError):
            return ()
        if not summary:
            return ()
        summary = _truncate_tokens(summary, self._token_limit)
        return (
            PromptContribution(
                key="memory.instructions",
                template_name="memory/read_path",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.SESSION,
                variables={"memory_root": str(self._root), "memory_summary": summary},
            ),
        )


def _truncate_tokens(text: str, limit: int) -> str:
    if estimate_text_tokens(text) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_text_tokens(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip()
