"""Event-loop-owned discovery cache: weak immutable identity or dynamic search information."""

from dataclasses import dataclass
from weakref import ReferenceType, ref

from corki.protocol.tools import ToolSpec
from corki.tools.registry import ToolRegistry
from corki.tools.search import ToolSearchTool, search_text


@dataclass(frozen=True)
class _DynamicSearchInfo:
    text: str
    output: dict
    source: str | None
    source_description: str | None

    @classmethod
    def from_spec(cls, spec: ToolSpec) -> "_DynamicSearchInfo":
        # Execution-only concurrency/budgets are not part of a loadable tool's
        # search info. They may change without rebuilding the scoring engine.
        return cls(
            search_text(spec),
            spec.as_response_tool(native_freeform=True),
            spec.source,
            spec.source_description,
        )


@dataclass(frozen=True)
class _CachedSearch:
    sources: tuple[ReferenceType | _DynamicSearchInfo, ...]
    handler: ToolSearchTool


class ToolSearchHandlerCache:
    """Own a complete discovery generation without retaining executable source handlers.

    No awaits or threads interleave collection, construction and publication.
    The default Include source policy is derived from the captured search info.
    """

    def __init__(self) -> None:
        self._cached: _CachedSearch | None = None

    def get_or_build(self, registry: ToolRegistry) -> ToolSearchTool:
        entries = registry.deferred_entries()
        sources: list[ReferenceType | _DynamicSearchInfo] = []
        dynamic_specs: dict[int, ToolSpec] = {}
        for index, (name, tool) in enumerate(entries):
            if getattr(tool, "immutable_search_metadata", False) is True:
                try:
                    sources.append(ref(tool))
                    continue
                except TypeError:
                    # Slotted extension handlers need not expose __weakref__.
                    pass
            spec = registry.spec(name)
            assert spec is not None
            dynamic_specs[index] = spec
            sources.append(_DynamicSearchInfo.from_spec(spec))
        key = tuple(sources)
        cached = self._cached
        same = (
            cached is not None
            and len(cached.sources) == len(key)
            and all(
                (old() is not None and old() is new())
                if isinstance(old, ReferenceType) and isinstance(new, ReferenceType)
                else old == new
                if isinstance(old, _DynamicSearchInfo) and isinstance(new, _DynamicSearchInfo)
                else False
                for old, new in zip(cached.sources, key, strict=True)
            )
        )
        if same:
            assert cached is not None
            specs = tuple(
                dynamic_specs.get(index, cached.handler._definitions[index])
                for index in range(len(entries))
            )
            if specs == cached.handler._definitions:
                return cached.handler
            # Rebind execution-only ToolSpec fields while retaining the exact
            # equivalent index. An old handler still owns its old definitions.
            handler = ToolSearchTool.from_specs(specs, index=cached.handler.index)
        else:
            captured: list[ToolSpec] = []
            for index, (name, _tool) in enumerate(entries):
                spec = dynamic_specs[index] if index in dynamic_specs else registry.spec(name)
                assert spec is not None
                captured.append(spec)
            handler = ToolSearchTool.from_specs(tuple(captured))
        # Schema capture, text generation and index build have all succeeded.
        self._cached = _CachedSearch(key, handler)
        return handler
