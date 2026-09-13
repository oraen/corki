"""Top-K BM25 lexical discovery over registered deferred tool metadata."""

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools.base import ToolContext
from corki.tools.bm25 import BM25Scorer
from corki.tools.discovery import TOOL_SEARCH_NAME
from corki.tools.registry import ToolRegistry
from corki.tools.search_sources import render_sources
from corki.tools.tokenizer import token_id, tokenize


def _tokens(text: str) -> list[str]:
    return tokenize(text)


def _schema_text(schema: Mapping) -> list[str]:
    parts = [str(schema.get("description", ""))]
    for name, child in sorted(schema.get("properties", {}).items()):
        parts.append(name)
        if isinstance(child, Mapping):
            parts.extend(_schema_text(child))
    if isinstance(schema.get("items"), Mapping):
        parts.extend(_schema_text(schema["items"]))
    for child in schema.get("anyOf", ()):
        if isinstance(child, Mapping):
            parts.extend(_schema_text(child))
    return parts


def search_text(spec: ToolSpec) -> str:
    """Index names, source, description and recursive property documentation."""

    if spec.search_text is not None:
        return spec.search_text
    if spec.input_kind == "freeform":
        return " ".join(
            part.strip()
            for part in (
                spec.name,
                spec.description,
                spec.namespace_description or "",
                (spec.freeform_format or {}).get("syntax", ""),
                spec.source or "",
            )
            if part.strip()
        )
    return " ".join(
        [
            spec.name,
            spec.name.replace("_", " "),
            spec.description,
            spec.source or "",
        ]
        + ([spec.namespace_description] if spec.namespace_description else [])
        + _schema_text(spec.parameters)
    )


@dataclass(frozen=True, slots=True)
class _IndexSnapshot:
    specs: tuple[ToolSpec, ...]
    scorer: BM25Scorer
    generation: int


class ToolSearchIndex:
    """Cache a generation by full definition equality, never just tool names."""

    def __init__(self) -> None:
        self._snapshot = _IndexSnapshot((), BM25Scorer(()), 0)

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return deepcopy(self._snapshot.specs)

    @property
    def generation(self) -> int:
        return self._snapshot.generation

    def search(self, specs: tuple[ToolSpec, ...], query: str, limit: int) -> tuple[ToolSpec, ...]:
        self.prepare(specs)
        return deepcopy(tuple(self._snapshot.specs[index] for index in self.rank(query, limit)))

    def prepare(self, specs: tuple[ToolSpec, ...]) -> None:
        """Build a complete index before publishing; no search call is executed."""
        snapshot = self._snapshot
        if specs != snapshot.specs:
            owned_specs = deepcopy(specs)
            scorer = BM25Scorer(
                (token_id(token) for token in _tokens(search_text(spec))) for spec in owned_specs
            )
            snapshot = _IndexSnapshot(owned_specs, scorer, snapshot.generation + 1)
            # Build and validate everything before publishing one coherent generation.
            self._snapshot = snapshot

    def rank(self, query: str, limit: int) -> tuple[int, ...]:
        """Query the already prepared generation without reading tool registrations."""
        return tuple(
            index
            for index, _score in self._snapshot.scorer.rank(
                (token_id(token) for token in _tokens(query)), limit
            )
        )


class ToolSearchTool:
    """Discover definitions; never execute a retrieved tool on the model's behalf."""

    def __init__(self, registry: ToolRegistry, *, include_sources: bool = True) -> None:
        self._initialize(
            tuple(spec for spec in registry.specs() if spec.exposure.is_deferred),
            include_sources=include_sources,
        )

    @classmethod
    def from_specs(
        cls,
        specs: tuple[ToolSpec, ...],
        *,
        index: ToolSearchIndex | None = None,
        include_sources: bool = True,
    ) -> "ToolSearchTool":
        """Bind frozen definitions, optionally reusing an equivalent discovery index."""
        handler = cls.__new__(cls)
        handler._initialize(specs, index=index, include_sources=include_sources)
        return handler

    def _initialize(
        self,
        specs: tuple[ToolSpec, ...],
        *,
        index: ToolSearchIndex | None = None,
        include_sources: bool = True,
    ) -> None:
        self._definitions = deepcopy(specs)
        self._include_sources = include_sources
        self._spec = self._build_spec()
        if index is None:
            index = ToolSearchIndex()
            index.prepare(self._definitions)
        self.index = index

    @property
    def spec(self) -> ToolSpec:
        return deepcopy(self._spec)

    def _build_spec(self) -> ToolSpec:
        sources = (
            "You have access to tools from the following sources:\n"
            + render_sources(self._definitions)
            + "\n"
            if self._include_sources
            else ""
        )
        return ToolSpec(
            TOOL_SEARCH_NAME,
            "# Tool discovery\n\nSearches over deferred tool metadata with BM25 and exposes "
            "matching tools for the next model call.\n\n"
            f"{sources}Some of the tools may not have been provided to you "
            "upfront, and you should use this tool (`tool_search`) to search for the required "
            "tools. For MCP tool discovery, always use `tool_search` instead of "
            "`list_mcp_resources` or `list_mcp_resource_templates`.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Tool metadata search query"},
                    "limit": {"type": "integer", "minimum": 1, "description": "Default 8"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        query = call.arguments["query"].strip()
        if not query:
            raise ValueError("query must not be empty")
        matches = tuple(
            deepcopy(self._definitions[index])
            for index in self.index.rank(
                query, min(call.arguments.get("limit", 8), len(self._definitions))
            )
        )
        # Search returns complete Top-K definitions. The context manager owns
        # window pressure; silently dropping a large hit makes it undiscoverable
        # even when the selected model has room for its schema.
        content = json.dumps(
            {"tools": [spec.as_chat_completion_tool() for spec in matches]},
            ensure_ascii=False,
        )
        return ToolResult(
            call.id,
            call.name,
            content,
            discovered_tools=matches,
            contains_external_context=True,
        )
