"""Per-pass parent execution settings and native memory-worker permission derivation."""

from dataclasses import dataclass
from pathlib import Path

from corki.config.managed_mcp import MCPRequirementsSnapshot
from corki.config.permissions import ExecutionPermissions
from corki.execution.backend import derive_memory_permissions


class MemorySandboxPolicyError(ValueError):
    """The owned memory job could not establish its worker execution policy."""


@dataclass(frozen=True, slots=True)
class MemoryPermissionSnapshot:
    """Capture one admitted caller, never consult later mutable service settings."""

    parent: ExecutionPermissions | None
    managed: MCPRequirementsSnapshot | None = None

    async def for_worker(self, root: Path) -> ExecutionPermissions | None:
        """Keep absent legacy host policy; otherwise require a valid derived profile."""
        if self.parent is None:
            return None
        try:
            return await derive_memory_permissions(self.parent, root)
        except Exception as exc:
            raise MemorySandboxPolicyError(f"failed_sandbox_policy: {exc}") from exc
