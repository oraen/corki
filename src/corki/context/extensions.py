"""Small context-contribution boundary used by optional feature packages."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from corki.prompting import PromptContribution


class ContextContributor(Protocol):
    def contributions(
        self,
        *,
        cwd: Path,
        user_input: str,
        realtime_active: bool,
    ) -> tuple[PromptContribution, ...]: ...
