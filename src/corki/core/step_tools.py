"""Process-local ownership for a sampling step; handler objects never enter checkpoints."""

from uuid import uuid4

from corki.tools.registry import ToolRegistry, ToolRegistrySnapshot


class StepToolState:
    """Keep only the latest binding; running calls independently retain their snapshot."""

    def __init__(self) -> None:
        self._key: str | None = None
        self._snapshot: ToolRegistrySnapshot | None = None

    def bind(self, snapshot: ToolRegistrySnapshot) -> str:
        self._key, self._snapshot = str(uuid4()), snapshot
        return self._key

    def resolve(self, key: str | None, registry: ToolRegistry) -> ToolRegistrySnapshot:
        """Cold recovery rebinds current handlers, still subject to saved-spec validation.

        Missing keys support old checkpoints and direct graph-node callers. A
        recorded call outcome remains authoritative; this does not replay it.
        """
        if self._snapshot is None or self._key != key:
            self._key, self._snapshot = key, registry.snapshot()
        return self._snapshot
