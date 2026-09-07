"""Executable tool interface and per-call context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.protocol.truncation import TruncationPolicy


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
