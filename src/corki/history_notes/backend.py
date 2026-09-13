"""Local history/notes execution contract and safe observation errors."""

from typing import Any, Protocol


class HistoryNotesError(ValueError):
    """Sanitized operation failure safe to return as a model observation."""


class RecoveryBackend(Protocol):
    """Shared execution port for local recovery backends."""

    async def call(
        self, action: str, arguments: dict[str, Any], *, session_id: str, budget: int
    ) -> Any:
        """Execute one operation using a trusted service identity and output budget."""
        ...

    async def aclose(self) -> None:
        """Release owned resources after active operations have been joined."""
        ...
