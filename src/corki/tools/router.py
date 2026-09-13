"""Pure candidate tool planning over a captured host registry generation."""

from dataclasses import replace

from corki.code_mode.tools import CodeModeExecTool, CodeModeWaitTool
from corki.protocol.tool_names import has_namespace_description, split_tool_name


def search_enabled(settings, info=None, *, namespace_tools=True):
    """Harness function search is controlled by configuration, not native capability."""
    return settings.tool_search_mode != "disabled"


class ToolRouterBuilder:
    def __init__(
        self,
        registry,
        service,
        search_cache,
        search_owner,
        code_mode_owner,
        *,
        namespace_tools=True,
    ):
        self.registry, self.service, self.search_cache = registry, service, search_cache
        self.search_owner, self.code_mode_owner = search_owner, code_mode_owner
        if not isinstance(namespace_tools, bool):
            raise ValueError("provider namespace_tools must be a bool")
        self.namespace_tools = namespace_tools

    def search_enabled(self, settings, info=None):
        return search_enabled(settings, info, namespace_tools=self.namespace_tools)

    def mode(self, settings, info=None):
        info = info or settings.model_context_info(settings.model)
        requested = info.tool_mode or settings.tool_mode
        if (
            not self.service.engine_available
            and requested == "code_mode"
            and not settings.code_mode_disable_fallback
        ):
            return "direct"
        return requested

    def build(self, snapshot, settings, info=None, *, mode=None, search=None):
        # Selected plans own ephemeral controls; replan from their original host
        # generation, never from the latest global directory or those controls.
        snapshot = snapshot.router_source or snapshot
        selected = self.mode(settings, info) if mode is None else mode
        if selected not in {"direct", "code_mode", "code_mode_only"}:
            raise ValueError("invalid saved tool mode")
        search = self.search_enabled(settings, info) if search is None else search
        if not isinstance(search, bool):
            raise ValueError("invalid saved tool search capability")
        # These are host-owned controls, not arbitrary similarly named tools.
        sources = snapshot.without_owners(frozenset({self.search_owner, self.code_mode_owner}))
        projected = []
        for spec in sources.specs():
            mask = sources.mcp_mask(spec.name)
            if mask is not None:
                spec = replace(
                    spec,
                    exposure=snapshot.namespace_policy.mcp_exposure(
                        spec.name,
                        mask,
                        search_enabled=search,
                        code_mode_only=selected == "code_mode_only",
                    ),
                )
            projected.append(spec)
        candidate = sources.derive(
            specs=tuple(projected), tool_mode=selected, search_enabled=search
        )
        collisions = [candidate.first_collision] if candidate.first_collision is not None else []
        if selected != "direct":
            collisions.extend(name for name in ("exec", "wait") if candidate.get(name) is not None)
            candidate = candidate.derive(remove=frozenset({"exec", "wait"}))
        if search and any(name != "tool_search" for name, _ in candidate.deferred_entries()):
            conflicts = tuple(
                spec.name
                for spec in candidate.specs()
                if spec.name == "tool_search" or split_tool_name(spec.name)[0] == "tool_search"
            )
            # Special model tools own their entire native namespace surface.
            if "tool_search" in conflicts:
                collisions.append("tool_search")
            collisions.extend(name for name in conflicts if name != "tool_search")
            candidate = candidate.derive(remove=frozenset(conflicts))
            candidate = candidate.derive(
                tools=(
                    self.search_cache.get_or_build(
                        candidate, include_sources=not settings.deferred_tool_world_state
                    ),
                )
            )
        if selected != "direct":
            candidate = candidate.derive(
                tools=(
                    CodeModeExecTool(self.service, registry=candidate),
                    CodeModeWaitTool(self.service),
                )
            )
        if settings.error_on_tool_collisions:
            if collisions:
                namespace, leaf = split_tool_name(collisions[0])
                raise ValueError(f"tool collision: {namespace or 'functions'}.{leaf}")
            self._check_namespaces(candidate, settings, info)
        return replace(candidate, router_source=snapshot)

    @staticmethod
    def _check_namespaces(candidate, settings, info):
        descriptions = {}
        for spec in candidate.specs():
            namespace, _ = split_tool_name(spec.name)
            description = spec.namespace_description
            if namespace is not None and has_namespace_description(description):
                previous = descriptions.setdefault(namespace, description)
                if previous != description:
                    raise ValueError(f"tool collision: {namespace}")
