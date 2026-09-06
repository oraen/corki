"""Bounded BM25 lexical discovery over registered deferred tool metadata."""

import json
import math
import re
from collections import Counter
from collections.abc import Mapping

from corki.context.tokens import estimate_text_tokens
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools.base import ToolContext
from corki.tools.discovery import TOOL_SEARCH_NAME
from corki.tools.registry import ToolRegistry

_TOKENS = re.compile(r"[^\W_]+", re.UNICODE)
_OUTPUT_TOKEN_LIMIT = 8_000


def _tokens(text: str) -> list[str]:
    return _TOKENS.findall(text.casefold())


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

    return spec.search_text or " ".join(
        [spec.name, spec.name.replace("_", " "), spec.description, spec.source or ""]
        + _schema_text(spec.parameters)
    )


class ToolSearchIndex:
    """Cache a generation by full definition equality, never just tool names."""

    def __init__(self) -> None:
        self.specs: tuple[ToolSpec, ...] = ()
        self.documents: list[Counter] = []
        self.frequencies: Counter = Counter()
        self.average_length = 0.0
        self.generation = 0

    def search(self, specs: tuple[ToolSpec, ...], query: str, limit: int) -> tuple[ToolSpec, ...]:
        if specs != self.specs:
            self.specs = specs
            self.documents = [Counter(_tokens(search_text(spec))) for spec in specs]
            self.frequencies = Counter(term for doc in self.documents for term in doc)
            self.average_length = sum(doc.total() for doc in self.documents) / max(len(specs), 1)
            self.generation += 1
        if not self.average_length:
            return ()
        scores = []
        for index, doc in enumerate(self.documents):
            score = 0.0
            for term in sorted(set(_tokens(query))):
                frequency = doc[term]
                if frequency:
                    df = self.frequencies[term]
                    idf = math.log(1 + (len(specs) - df + 0.5) / (df + 0.5))
                    norm = 1.5 * (0.25 + 0.75 * doc.total() / self.average_length)
                    score += idf * frequency * 2.5 / (frequency + norm)
            if score > 0:
                scores.append((-score, index))
        return tuple(specs[index] for _, index in sorted(scores)[:limit])


class ToolSearchTool:
    """Discover definitions; never execute a retrieved tool on the model's behalf."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self.index = ToolSearchIndex()
        self._spec = self._build_spec()

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def _build_spec(self) -> ToolSpec:
        sources = sorted(
            {
                spec.source
                for spec in self._registry.specs()
                if spec.exposure.is_deferred and spec.source
            }
        )
        return ToolSpec(
            TOOL_SEARCH_NAME,
            "Search deferred tool metadata with BM25 and load matching definitions for the next "
            "model call. Use this for MCP tool discovery, not list_mcp_resources or templates. "
            "Search again if a definition is no longer available after compaction. Sources: "
            + ", ".join(sources)[:4_000],
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
        specs = tuple(spec for spec in self._registry.specs() if spec.exposure.is_deferred)
        matches = self.index.search(specs, query, min(call.arguments.get("limit", 8), len(specs)))
        selected: list[ToolSpec] = []
        definitions = []
        for spec in matches:
            candidate = [*definitions, spec.as_chat_completion_tool()]
            if (
                estimate_text_tokens(json.dumps(candidate, ensure_ascii=False))
                <= _OUTPUT_TOKEN_LIMIT
            ):
                definitions = candidate
                selected.append(spec)
        content = json.dumps(
            {"tools": definitions, "omitted_for_budget": len(matches) - len(selected)},
            ensure_ascii=False,
        )
        return ToolResult(call.id, call.name, content, discovered_tools=tuple(selected))
