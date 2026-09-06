"""Executable tool interface and per-call context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from corki.protocol.tools import ToolCall, ToolResult, ToolSpec


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Immutable environment snapshot available to a tool invocation."""

    cwd: Path


class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult: ...
