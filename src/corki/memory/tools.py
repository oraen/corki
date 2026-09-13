"""Dedicated memory tools with internal grouping and ordinary function-call contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import NoReturn

from corki.memory.backend import LocalMemoryBackend, MemoryNoteExistsError, MemoryNotFoundError
from corki.memory.tool_contracts import (
    cursor_offset,
    input_schema,
    output_schema,
    parse_arguments,
    result_limit,
)
from corki.protocol.tools import CodeModeOutput, ToolCall, ToolResult, ToolSpec
from corki.tools.base import ToolContext
from corki.tools.errors import FatalToolError


class _MemoryTool:
    operation: str
    description: str

    def __init__(self, backend: LocalMemoryBackend) -> None:
        self._backend = backend

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=f"memories::{self.operation}",
            description=self.description,
            parameters=input_schema(self.operation),
            namespace_description="Tools in the memories namespace.",
            output_schema=output_schema(self.operation),
        )

    def parse_call_arguments(self, call: ToolCall) -> Mapping[str, object]:
        """Own native Value/serde-style parsing, separate from model-facing hints."""
        return parse_arguments(call, self.operation)

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        args = call.arguments
        try:
            if self.operation == "add_ad_hoc_note":
                self._backend.add_note(args["filename"], args["note"])
                payload = {}
            elif self.operation == "read":
                payload = await self._backend.read(
                    args["path"],
                    line_offset=1 if args.get("line_offset") is None else args["line_offset"],
                    max_lines=args.get("max_lines"),
                    max_tokens=20_000,
                )
            elif self.operation == "list":
                payload = self._backend.list(
                    args.get("path"),
                    cursor=cursor_offset(args.get("cursor")),
                    limit=result_limit(args.get("max_results"), 2_000),
                )
                payload["entries"] = [
                    {"path": entry["path"], "entry_type": entry["type"]}
                    for entry in payload["entries"]
                ]
                _string_cursor(payload)
            else:
                mode = args.get("match_mode") or {"type": "any"}
                kind = mode["type"]
                payload = await self._backend.search(
                    tuple(args["queries"]),
                    path=args.get("path"),
                    match_mode=kind,
                    within_lines=mode["line_count"] if kind == "all_within_lines" else 1,
                    context_lines=0 if args.get("context_lines") is None else args["context_lines"],
                    case_sensitive=True
                    if args.get("case_sensitive") is None
                    else args["case_sensitive"],
                    normalized=False if args.get("normalized") is None else args["normalized"],
                    cursor=cursor_offset(args.get("cursor")),
                    limit=result_limit(args.get("max_results"), 200),
                )
                payload["match_mode"] = {
                    "type": kind,
                    **({"line_count": mode["line_count"]} if kind == "all_within_lines" else {}),
                }
                _string_cursor(payload)
        except (OSError, UnicodeError, ValueError) as exc:
            _backend_error(exc)
        return ToolResult(
            call.id,
            call.name,
            json.dumps(payload, ensure_ascii=False),
            code_mode_output=CodeModeOutput(payload),
        )


class MemoryListTool(_MemoryTool):
    """List immediate files/directories using string pagination cursors."""

    operation = "list"
    description = "List immediate files and directories under a path in the Corki memories store."


class MemoryReadTool(_MemoryTool):
    """Read a bounded line range with the native fixed backend token budget."""

    operation = "read"
    description = (
        "Read a Corki memory file by relative path, optionally starting at a 1-indexed "
        "line offset and limiting the number of lines returned."
    )


class MemorySearchTool(_MemoryTool):
    """Search scoped files with a tagged substring-match mode."""

    operation = "search"
    description = (
        "Search Corki memory files for substring matches, optionally normalizing separators "
        "or requiring all query substrings on the same line or within a line window."
    )


class MemoryAddNoteTool(_MemoryTool):
    """Create one note only for an explicit user memory-update request."""

    operation = "add_ad_hoc_note"
    description = (
        "Create one append-only ad-hoc memory note after the user explicitly asks Corki "
        "to remember, forget, or update something."
    )


def memory_tools(backend: LocalMemoryBackend) -> tuple[object, ...]:
    """Build four internally grouped tools; all tools use exclusive execution."""
    return (
        MemoryAddNoteTool(backend),
        MemoryListTool(backend),
        MemoryReadTool(backend),
        MemorySearchTool(backend),
    )


def _string_cursor(payload: dict) -> None:
    if payload["next_cursor"] is not None:
        payload["next_cursor"] = str(payload["next_cursor"])


def _backend_error(error: Exception) -> NoReturn:
    # Domain errors are dispatch failures (including rejected Code Mode promises),
    # not successful handler-returned error values. Keep real I/O fatal for Direct.
    if isinstance(error, (MemoryNotFoundError, MemoryNoteExistsError)):
        raise ValueError(str(error)) from error
    if isinstance(error, (OSError, UnicodeError)):
        raise FatalToolError(f"I/O error while reading memories: {error}") from error
    raise error
