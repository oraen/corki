"""Source-preserving MCP declarations, ordered actions and immutable resolved views."""

from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path

from corki.config import MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements, MCPServerSource
from corki.mcp.model_access import CODEX_APPS_SERVER

_KINDS = ("plugin", "selected_plugin", "config", "compatibility", "extension")


@dataclass(frozen=True, slots=True)
class MCPCatalogSource:
    """Trusted declaring component, separate from remote tool metadata and route names."""

    kind: str = "config"
    identity: str | None = None
    order: int = 0
    host_root: Path | None = None
    agent_plugin: bool = False
    display_name: str | None = None
    host_owned_apps: bool = False

    def __post_init__(self) -> None:
        if type(self.host_owned_apps) is not bool or (
            self.host_owned_apps and self.kind != "extension"
        ):
            raise ValueError("host-owned Apps requires an explicit extension source")
        if self.kind not in _KINDS:
            raise ValueError("unknown MCP catalog source")
        if type(self.order) is not int or self.order < 0:
            raise ValueError("MCP source order must be a nonnegative integer")
        if self.kind != "config" and (not isinstance(self.identity, str) or not self.identity):
            raise ValueError("MCP source requires its declaring component identity")
        if self.kind == "config" and self.identity is not None:
            raise ValueError("config MCP source has no package or extension identity")
        if self.host_root is not None and not isinstance(self.host_root, Path):
            raise ValueError("MCP host root must be a host-discovered path")
        if type(self.agent_plugin) is not bool or (
            self.agent_plugin and self.kind not in ("plugin", "selected_plugin")
        ):
            raise ValueError("MCP agent plugin attribution requires a typed plugin source")
        if self.display_name is not None:
            if not isinstance(self.display_name, str) or self.kind not in _KINDS[:2]:
                raise ValueError("MCP plugin display name requires a typed plugin source")
            self.display_name.encode("utf-8")

    @property
    def plugin_display_names(self) -> tuple[str, ...]:
        if self.kind not in _KINDS[:2]:
            return ()
        # Legacy host declarations only supplied an ID. Never consult an
        # unrelated local package to guess a selected source's display name.
        return (self.identity if self.display_name is None else self.display_name,)

    @property
    def precedence(self) -> tuple[int, int]:
        """Earlier plugins win; later extension actions win; same priority is stable."""
        tier = _KINDS.index(self.kind)
        order = -self.order if tier < 2 else self.order if tier == 4 else 0
        return tier, order

    def requirement_source(self, raw_name: str) -> MCPServerSource:
        """Use raw declaration/package identity when applying controller requirements."""
        return MCPServerSource(raw_name, self.identity if self.kind in _KINDS[:2] else None)

    def is_host_owned_apps(self, settings: MCPServerSettings) -> bool:
        return (
            settings.name == CODEX_APPS_SERVER
            and settings.environment_id == "local"
            and (self.kind == "compatibility" or self.kind == "extension" and self.host_owned_apps)
        )


@dataclass(frozen=True, slots=True)
class MCPRegistration:
    """One declaration before winner resolution; settings.name is the logical name."""

    settings: MCPServerSettings
    source: MCPCatalogSource = MCPCatalogSource()

    def __post_init__(self) -> None:
        if not isinstance(self.settings, MCPServerSettings) or not isinstance(
            self.source, MCPCatalogSource
        ):
            raise ValueError("MCP registration requires typed settings and host source")
        object.__setattr__(self, "settings", deepcopy(self.settings))

    @property
    def name(self) -> str:
        """The raw logical server name, independent of any package prefix."""
        return self.settings.name


@dataclass(frozen=True, slots=True)
class MCPRemoval:
    """An ordered host overlay action, not deletion of unrelated declarations."""

    name: str
    source: MCPCatalogSource

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("MCP removal needs a logical name")
        if not isinstance(self.source, MCPCatalogSource) or self.source.kind not in _KINDS[3:]:
            raise ValueError("only compatibility and extension overlays remove MCP declarations")


MCPAction = MCPRegistration | MCPRemoval


@dataclass(frozen=True, slots=True)
class MCPCatalogConflict:
    """Same-tier contenders plus the final outcome, including cross-tier overrides."""

    name: str
    outcome: MCPAction
    contenders: tuple[MCPAction, ...]


class MCPCatalog:
    """Captured declaration history and winners; exposed values are detached copies."""

    def __init__(
        self, actions: tuple[MCPAction, ...] = (), *, disabled: frozenset[str] = frozenset()
    ):
        actions = deepcopy(tuple(actions))
        if any(not isinstance(action, (MCPRegistration, MCPRemoval)) for action in actions):
            raise ValueError("MCP catalog actions must be typed")
        if any(not isinstance(name, str) or not name for name in disabled):
            raise ValueError("MCP disabled names must be strings")
        self._actions = tuple(sorted(actions, key=lambda action: action.source.precedence))
        winners = {}
        tiers = defaultdict(list)
        for action in self._actions:
            winners[action.name] = action
            tiers[action.name, action.source.precedence[0]].append(action)
        self._conflicts = tuple(
            MCPCatalogConflict(name, winners[name], tuple(contenders))
            for (name, _), contenders in sorted(tiers.items())
            if len(contenders) > 1
        )
        vetoes = set(disabled)
        servers = []
        for name, winner in sorted(winners.items()):
            if isinstance(winner, MCPRemoval):
                continue
            if not winner.settings.enabled or name in vetoes:
                winner = replace(winner, settings=replace(winner.settings, enabled=False))
                if winner.source.kind != "selected_plugin":
                    vetoes.add(name)
            servers.append(winner)
        self._servers = tuple(servers)
        # Explicit/materialized vetoes differ from enablement derived from the
        # current declarations. A policy transform must resolve the latter anew.
        self._input_disabled = frozenset(disabled)
        self._disabled = frozenset(vetoes)

    @property
    def servers(self) -> tuple[MCPRegistration, ...]:
        """Return winning registrations, retaining disabled winners for later overlays."""
        return deepcopy(self._servers)

    @property
    def conflicts(self) -> tuple[MCPCatalogConflict, ...]:
        """Return deterministic diagnostics without exposing captured settings to mutation."""
        return deepcopy(self._conflicts)

    def extend(self, *actions: MCPAction) -> "MCPCatalog":
        """Resolve new host actions while preserving previously materialized disabled vetoes."""
        return MCPCatalog((*self._actions, *actions), disabled=self._disabled)

    def with_default_cwd(self, cwd: Path) -> "MCPCatalog":
        """Capture local cwd only; a remote declaration cannot inherit a controller path."""
        return MCPCatalog(
            tuple(
                replace(action, settings=replace(action.settings, cwd=action.settings.cwd or cwd))
                if isinstance(action, MCPRegistration) and action.settings.environment_id == "local"
                else action
                for action in self._actions
            ),
            disabled=self._input_disabled,
        )

    def without_plugins(self) -> "MCPCatalog":
        """Remove package contributions before resolution, not a global name veto."""
        return MCPCatalog(
            tuple(action for action in self._actions if action.source.kind not in _KINDS[:2]),
            disabled=self._input_disabled,
        )

    def constrain(self, requirements: MCPRequirements) -> "MCPCatalog":
        """Apply controller policy to declarations before source winner resolution.

        Compatibility/extension declarations are explicit trusted host contributions,
        not user config; native controller constraints apply to config and packages.
        Environment attachment authority is a separate gate, not inferred here.
        """
        actions = []
        for action in self._actions:
            if isinstance(action, MCPRegistration) and action.source.kind in _KINDS[:3]:
                allowed = requirements.allows(
                    action.settings, action.source.requirement_source(action.name)
                )
                settings = replace(
                    action.settings,
                    enabled=action.settings.enabled and allowed,
                    disabled_reason=None if allowed else "managed requirements",
                )
                action = replace(action, settings=settings)
            actions.append(action)
        # Runtime builds the controller-owned base before adding host overlays.
        # This preserves a disabled base winner as a name veto on later overlays.
        base = MCPCatalog(
            tuple(action for action in actions if action.source.kind in _KINDS[:3]),
            disabled=self._disabled,
        )
        return base.extend(*(action for action in actions if action.source.kind in _KINDS[3:]))

    def constrain_environments(
        self, requirements_for: Callable[[str], MCPRequirements]
    ) -> "MCPCatalog":
        """Apply selected owner authority to every source before resolving winners."""
        actions = []
        for action in self._actions:
            if isinstance(action, MCPRegistration) and action.settings.enabled:
                requirements = requirements_for(action.settings.environment_id)
                if not requirements.allows(
                    action.settings, action.source.requirement_source(action.name)
                ):
                    action = replace(
                        action,
                        settings=replace(
                            action.settings,
                            enabled=False,
                            disabled_reason="environment requirements",
                        ),
                    )
            actions.append(action)
        return MCPCatalog(tuple(actions), disabled=self._disabled)

    def transform_settings(
        self, transform: Callable[[MCPRegistration], MCPServerSettings]
    ) -> "MCPCatalog":
        """Update source-scoped settings while retaining hidden declarations and removals."""
        return MCPCatalog(
            tuple(
                replace(action, settings=transform(action))
                if isinstance(action, MCPRegistration)
                else action
                for action in self._actions
            ),
            disabled=self._input_disabled,
        )

    def materialize(self, servers: tuple[MCPServerSettings, ...]) -> "MCPCatalog":
        """Replace effective settings while retaining known sources, as native refresh does."""
        sources = {server.name: server.source for server in self._servers}
        return MCPCatalog(
            tuple(
                MCPRegistration(
                    server, replace(sources.get(server.name, MCPCatalogSource()), order=0)
                )
                for server in servers
            )
        )
