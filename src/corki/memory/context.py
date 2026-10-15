"""Prompt contribution for the compact long-term memory routing index."""

from __future__ import annotations

import json
from pathlib import Path

from corki.memory.inputs import truncate_memory_text
from corki.prompting import PromptContribution, PromptPhase, PromptRole, PromptSlot


class MemoryContextContributor:
    """Inject only the compact index; detailed memory stays progressively loaded."""

    initial_context_keys = ("memory.instructions",)

    def __init__(
        self, root: Path, *, enabled: bool, token_limit: int, history_database: Path | None = None
    ) -> None:
        self._root = root
        self._enabled = enabled
        self._token_limit = token_limit
        self._history_database = (
            history_database.absolute() if history_database is not None else None
        )

    def contributions(self, *, cwd: Path, user_input: str, realtime_active: bool):
        del cwd, user_input, realtime_active
        if not self._enabled:
            return ()
        try:
            summary = (self._root / "memory_summary.md").read_bytes().decode("utf-8").strip()
        except (FileNotFoundError, OSError, UnicodeError):
            return ()
        if not summary:
            return ()
        summary = truncate_memory_text(summary, self._token_limit)
        return (
            PromptContribution(
                key="memory.instructions",
                content_kind="memories.instructions",
                template_name="memory/read_path",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.SESSION,
                phase=PromptPhase.EXTENSION,
                variables={
                    "memory_root": str(self._root),
                    "memory_summary": summary,
                    "history_database": json.dumps(str(self._history_database), ensure_ascii=False)
                    if self._history_database is not None
                    else "unavailable",
                },
            ),
        )
