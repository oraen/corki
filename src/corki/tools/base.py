"""Executable tool interface and per-call context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from corki.config.permissions import ExecutionPermissions
from corki.protocol.collaboration import ModeKind
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.protocol.truncation import TruncationPolicy
from corki.protocol.user_input import UserInputQuestion
from corki.shell import Shell

if TYPE_CHECKING:
    from corki.mcp.resource_binding import MCPResourceBinding


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Immutable environment snapshot available to a tool invocation."""

    cwd: Path
    # Host-owned notification at an admitted external call boundary, never model input.
    on_external_context: Callable[[], Awaitable[None]] | None = None
    model_output_policy: TruncationPolicy = field(default_factory=TruncationPolicy)
    supports_image_input: bool = True
    supports_audio_input: bool = False
    supports_image_detail_original: bool = False
    shell: Shell | None = None
    remaining_context_tokens: Callable[[], Awaitable[int | None]] | None = None
    execution_permissions: ExecutionPermissions | None = None
    write_stdin_approval: bool = False
    honor_exec_policy_allow_rules: bool = True
    on_warning: Callable[[str], Awaitable[None]] | None = None
    on_patch_started: Callable[[], None] | None = None
    mcp_resources: MCPResourceBinding | None = None
    orchestrator_mcp_enabled: bool = True
    collaboration_mode: ModeKind = "default"
    is_non_root_agent: bool = False
    request_user_input: (
        Callable[[str, tuple[UserInputQuestion, ...], bool], Awaitable[dict]] | None
    ) = None
    before_tool: Callable[[ToolCall, object], Awaitable[ToolCall | str]] | None = None


class Tool(Protocol):
    """Executable handler; metadata is dynamic unless explicitly declared immutable.

    Tools may opt into identity-based discovery caching with the class attribute
    ``immutable_search_metadata = True``. All schema and search metadata must then
    stay immutable for that instance, including nested mappings returned by spec.
    Non-weak-referenceable instances use value caching even when they opt in.
    """

    @property
    def spec(self) -> ToolSpec: ...

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult: ...


@runtime_checkable
class ToolReadiness(Protocol):
    """Optional startup wait, without invoking or authorizing a tool call."""

    async def wait_until_ready(self) -> None: ...


@runtime_checkable
class ToolCallArgumentParser(Protocol):
    """Optional handler-owned parser when public schema hints differ from accepted inputs.

    The executor passes an isolated call and accepts only decoded arguments back;
    durable identity, raw input and admission still belong to the host. Tools not
    implementing this protocol retain ordinary JSON/schema validation. An opted-in
    handler may return None to preserve an omitted argument object (not JSON null).
    """

    def parse_call_arguments(self, call: ToolCall) -> Mapping[str, object] | None: ...
