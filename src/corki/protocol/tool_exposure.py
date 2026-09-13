"""Immutable host exposure projection; this is not execution authorization."""

from dataclasses import dataclass, replace

from corki.protocol.tool_names import split_tool_name
from corki.protocol.tools import ToolExposure, ToolSpec

CODE_MODE_EXPOSURES = frozenset(
    {ToolExposure.DIRECT, ToolExposure.DEFERRED, ToolExposure.CODE_MODE_ONLY}
)


@dataclass(frozen=True)
class ToolNamespacePolicy:
    direct_only: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()

    def __post_init__(self):
        for field in ("direct_only", "excluded"):
            values = getattr(self, field)
            if not isinstance(values, (list, tuple)) or not all(
                isinstance(value, str) for value in values
            ):
                raise ValueError(f"{field} tool namespaces must be an array of strings")
            object.__setattr__(self, field, tuple(values))

    def project(self, spec: ToolSpec) -> ToolSpec:
        namespace, _ = split_tool_name(spec.name)
        if (
            namespace if namespace is not None else "functions"
        ) in self.direct_only and spec.exposure in CODE_MODE_EXPOSURES:
            return replace(spec, exposure=ToolExposure.DIRECT_MODEL_ONLY)
        return spec

    def includes_nested(self, name: str) -> bool:
        namespace, _ = split_tool_name(name)
        return (namespace if namespace is not None else "functions") not in self.excluded

    def mcp_exposure(
        self,
        name: str,
        omitted: tuple[str, ...] | None,
        *,
        search_enabled: bool,
        code_mode_only: bool,
    ) -> ToolExposure:
        """Resolve a registered MCP server's surfaces before generic projection.

        A namespace direct-only override must not restore an omitted direct bit.
        Namespace exclusion is deliberately separate: it changes nested publication,
        not this registered exposure or the search-vs-direct selection.
        """
        remaining = {"direct", "deferred", "code_mode"}.difference(omitted or ())
        namespace, _ = split_tool_name(name)
        if (namespace if namespace is not None else "functions") in self.direct_only:
            remaining.difference_update({"deferred", "code_mode"})
        if (
            search_enabled
            and "deferred" in remaining
            and (not code_mode_only or "code_mode" in remaining)
        ):
            remaining.discard("direct")
        else:
            remaining.discard("deferred")
        return {
            frozenset(): ToolExposure.HIDDEN,
            frozenset({"direct"}): ToolExposure.DIRECT_MODEL_ONLY,
            frozenset({"deferred"}): ToolExposure.DEFERRED_MODEL_ONLY,
            frozenset({"code_mode"}): ToolExposure.CODE_MODE_ONLY,
            frozenset({"direct", "code_mode"}): ToolExposure.DIRECT,
            frozenset({"deferred", "code_mode"}): ToolExposure.DEFERRED,
        }[frozenset(remaining)]
