"""Host configuration filters raw MCP names, before model-name normalization."""

from dataclasses import dataclass

from corki.config import MCPServerSettings


@dataclass(frozen=True, slots=True)
class MCPToolFilter:
    """Generation-owned exact allow/deny sets; a configured empty allowlist denies all."""

    enabled: frozenset[str] | None = None
    disabled: frozenset[str] = frozenset()

    @classmethod
    def from_settings(cls, settings: MCPServerSettings) -> "MCPToolFilter":
        """Capture policy independently of desired settings or a mutable transport."""
        return cls(
            None if settings.enabled_tools is None else frozenset(settings.enabled_tools),
            frozenset(settings.disabled_tools or ()),
        )

    def allows(self, raw_name: str) -> bool:
        """Deny wins; names are neither stripped nor case-folded nor canonicalized."""
        return (self.enabled is None or raw_name in self.enabled) and raw_name not in self.disabled
