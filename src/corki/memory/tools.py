"""Optional dedicated tools for progressive long-term memory retrieval."""

from __future__ import annotations

import json

from corki.memory.backend import AD_HOC_FILENAME_PATTERN, LocalMemoryBackend
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools.base import ToolContext


class MemoryListTool:
    def __init__(self, backend: LocalMemoryBackend) -> None:
        self._backend = backend

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_list",
            description="List files or directories inside Corki's scoped long-term memory store.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "cursor": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "required": [],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        return _result(
            call,
            lambda: self._backend.list(
                _optional_string(call.arguments.get("path")),
                cursor=int(call.arguments.get("cursor", 0)),
                limit=int(call.arguments.get("limit", 2_000)),
            ),
        )


class MemoryReadTool:
    def __init__(self, backend: LocalMemoryBackend) -> None:
        self._backend = backend

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_read",
            description="Read a bounded line range from one scoped Corki memory file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line_offset": {"type": "integer", "minimum": 1},
                    "max_lines": {"type": "integer", "minimum": 1},
                    "max_tokens": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
            output_char_budget=80_000,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        try:
            payload = await self._backend.read(
                str(call.arguments["path"]),
                line_offset=int(call.arguments.get("line_offset", 1)),
                max_lines=(
                    int(call.arguments["max_lines"]) if "max_lines" in call.arguments else None
                ),
                max_tokens=int(call.arguments.get("max_tokens", 20_000)),
            )
            return ToolResult(call.id, call.name, json.dumps(payload, ensure_ascii=False))
        except (OSError, UnicodeError, ValueError) as exc:
            return ToolResult(call.id, call.name, str(exc), is_error=True)


class MemorySearchTool:
    def __init__(self, backend: LocalMemoryBackend) -> None:
        self._backend = backend

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_search",
            description=(
                "Search Corki memory files for bounded substring matches. Supports any query, "
                "all queries on one line, or all queries inside a small line window."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "path": {"type": "string"},
                    "match_mode": {
                        "type": "string",
                        "enum": ["any", "all_on_same_line", "all_within_lines"],
                    },
                    "within_lines": {"type": "integer", "minimum": 1},
                    "context_lines": {"type": "integer", "minimum": 0},
                    "case_sensitive": {"type": "boolean"},
                    "normalized": {"type": "boolean"},
                    "cursor": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["queries"],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
            output_char_budget=80_000,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        try:
            payload = await self._backend.search(
                tuple(str(value) for value in call.arguments["queries"]),
                path=_optional_string(call.arguments.get("path")),
                match_mode=str(call.arguments.get("match_mode", "any")),
                within_lines=int(call.arguments.get("within_lines", 1)),
                context_lines=int(call.arguments.get("context_lines", 0)),
                case_sensitive=bool(call.arguments.get("case_sensitive", True)),
                normalized=bool(call.arguments.get("normalized", False)),
                cursor=int(call.arguments.get("cursor", 0)),
                limit=int(call.arguments.get("limit", 200)),
            )
            return ToolResult(call.id, call.name, json.dumps(payload, ensure_ascii=False))
        except (OSError, UnicodeError, ValueError) as exc:
            return ToolResult(call.id, call.name, str(exc), is_error=True)


class MemoryAddNoteTool:
    def __init__(self, backend: LocalMemoryBackend) -> None:
        self._backend = backend

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_add_note",
            description=(
                "Add one ad-hoc long-term memory update note. Use only when the user explicitly "
                "asks Corki to remember, update, or forget something."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "minLength": 24,
                        "maxLength": 128,
                        "pattern": f"^{AD_HOC_FILENAME_PATTERN}$",
                        "description": (
                            "New file in YYYY-MM-DDTHH-MM-SS-<slug>.md format. "
                            "Use ASCII digits in the timestamp and a 1–80 character slug "
                            "of lowercase ASCII letters, digits and hyphens, starting with "
                            "a letter or digit. Existing files are never overwritten."
                        ),
                    },
                    "note": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Verbatim Markdown for the explicit memory update request.",
                    },
                },
                "required": ["filename", "note"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        return _result(
            call,
            lambda: self._backend.add_note(
                str(call.arguments["filename"]), str(call.arguments["note"])
            ),
        )


def memory_tools(backend: LocalMemoryBackend) -> tuple[object, ...]:
    return (
        MemoryAddNoteTool(backend),
        MemoryListTool(backend),
        MemoryReadTool(backend),
        MemorySearchTool(backend),
    )


def _result(call: ToolCall, operation) -> ToolResult:
    try:
        payload = operation()
        return ToolResult(call.id, call.name, json.dumps(payload, ensure_ascii=False))
    except (OSError, UnicodeError, ValueError) as exc:
        return ToolResult(call.id, call.name, str(exc), is_error=True)


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
