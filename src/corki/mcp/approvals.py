"""MCP user review using ordinary server configuration and typed remote hints.

This is not Guardian or managed permission enforcement. Never + disabled sandbox
auto-approves even Prompt, as in Codex; OnRequest activates the per-tool decision.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from corki.config import MCPServerSettings
from corki.mcp.client import MCPProtocolError
from corki.mcp.elicitation import ElicitationRouter


@dataclass(frozen=True, slots=True)
class MCPToolAnnotations:
    """Typed remote hints, not authority to choose the host's approval mode."""

    read_only: bool | None = None
    destructive: bool | None = None
    open_world: bool | None = None

    @classmethod
    def from_mapping(cls, annotations: object) -> "MCPToolAnnotations":
        """Match the optional boolean fields of the native MCP annotation type."""
        if annotations is None:
            return cls()
        if not isinstance(annotations, Mapping):
            raise ValueError("MCP annotations must be an object")
        values = tuple(
            annotations.get(key) for key in ("readOnlyHint", "destructiveHint", "openWorldHint")
        )
        if any(value is not None and type(value) is not bool for value in values):
            raise ValueError("MCP approval annotations must be booleans")
        return cls(*values)

    def requires_approval(self, mode: str) -> bool:
        """Evaluate native mode ordering, including conflicting hints."""
        if mode == "approve":
            return False
        if mode == "prompt":
            return True
        if mode == "writes":
            return self.read_only is not True
        if self.destructive is True:
            return True
        if self.read_only is True:
            return False
        return self.destructive is not False or self.open_world is not False


@dataclass(frozen=True, slots=True)
class MCPToolApprovalDecision:
    """A reviewed session grant, not yet applied to the Runtime's authority."""

    session_key: tuple[str, str | None, str | None, str]
    persistent: bool = False


class MCPToolApprovals:
    """Runtime-owned grants scoped by server, connector, account and raw tool."""

    def __init__(self, policy: str, router: ElicitationRouter) -> None:
        if policy not in ("never", "on-request"):
            raise ValueError("MCP approval_policy must be never or on-request")
        self._policy = policy
        self._router = router
        self._session: set[tuple[str, str | None, str | None, str]] = set()

    async def check(
        self,
        settings: MCPServerSettings,
        name: str,
        arguments: Mapping[str, Any] | None,
        annotations: MCPToolAnnotations,
        *,
        approval_mode: str | None = None,
        connector_id: str | None = None,
        link_id: str | None = None,
        allow_persistent: bool = True,
    ) -> MCPToolApprovalDecision | None:
        """Wait for review without holding catalog authority or recording grants."""
        mode = approval_mode or dict(settings.tool_approval_modes).get(
            name, settings.default_tools_approval_mode or "auto"
        )
        key = settings.name, connector_id, link_id, name
        can_remember = mode == "auto"
        if self._policy == "never" or not annotations.requires_approval(mode):
            return
        if can_remember and key in self._session:
            return
        properties = {}
        if can_remember:
            properties["remember"] = {
                "type": "boolean",
                "title": "Allow this tool for this session",
                "default": False,
            }
            if allow_persistent:
                properties["persist"] = {
                    "type": "string",
                    "title": "Approval duration (overrides the legacy remember checkbox)",
                    "enum": ["once", "session", "always"],
                    "description": "always saves this tool's approval in your configuration",
                }
        metadata = {
            "codex_approval_kind": "mcp_tool_call",
            "tool_params": dict(arguments) if arguments is not None else None,
        }
        if connector_id is not None:
            metadata["connector_id"] = connector_id
        if link_id is not None:
            metadata["link_id"] = link_id
        if can_remember:
            metadata["persist"] = ["session", "always"] if allow_persistent else "session"
        try:
            response = await self._router.request_tool_approval(
                settings.name,
                {
                    "mode": "form",
                    "message": f'Allow the {settings.name} MCP server to run tool "{name}"?',
                    "requestedSchema": {"type": "object", "properties": properties},
                    "_meta": metadata,
                },
            )
        except Exception as error:
            # Delivery errors may include host-private data. Match MCP failure
            # observations without exposing it or turning cancellation into data.
            raise MCPProtocolError(f"MCP tool approval failed: {type(error).__name__}") from error
        action = response.get("action")
        if action != "accept":
            reason = "cancelled" if action == "cancel" else "rejected"
            raise MCPProtocolError(f"user {reason} MCP tool call")
        content = response.get("content")
        metadata = response.get("_meta")
        scope = metadata.get("persist") if isinstance(metadata, Mapping) else None
        if scope not in ("session", "always"):
            scope = content.get("persist") if isinstance(content, Mapping) else None
        if can_remember and scope in ("session", "always"):
            return MCPToolApprovalDecision(key, scope == "always" and allow_persistent)
        if scope == "once":
            return None
        remember = isinstance(content, Mapping) and content.get("remember") is True
        remember = remember or (
            isinstance(metadata, Mapping) and metadata.get("persist") == "session"
        )
        if can_remember and remember:
            return MCPToolApprovalDecision(key)
        return None

    async def apply(self, decision: MCPToolApprovalDecision | None, *, persist=None) -> None:
        """Apply accepted grants only after the prepared catalog revision is checked."""
        if decision is not None:
            if decision.persistent and persist is not None:
                await persist(decision.session_key)
            self._session.add(decision.session_key)
