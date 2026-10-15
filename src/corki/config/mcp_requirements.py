"""Immutable host authority for native legacy and typed MCP server identities.

This is separate from user tool filters and approval choices. Typed positional
argument/regex matchers are enforced before transport construction. Managed
configuration-layer loading remains separate from this host-owned policy object.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from corki.config.features import MCPServerSettings
from corki.config.mcp_matchers import MCPCommandMatcher, MCPUrlMatcher, MCPValueMatcher
from corki.config.mcp_shapes import identity_fields, validate_requirements_shape


@dataclass(frozen=True, slots=True)
class MCPServerSource:
    """Raw declaration identity retained by the host, not inferred from a route name."""

    raw_name: str
    plugin_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw_name, str) or not self.raw_name:
            raise ValueError("MCP source requires a raw server name")
        if self.plugin_id is not None and (
            not isinstance(self.plugin_id, str) or not self.plugin_id
        ):
            raise ValueError("MCP plugin source requires a package identity")


@dataclass(frozen=True, slots=True)
class MCPServerIdentity:
    """Exact transport identity; legacy command identity intentionally ignores args."""

    transport: str
    value: str

    def __post_init__(self) -> None:
        if self.transport not in ("stdio", "http") or not isinstance(self.value, str):
            raise ValueError("MCP identity requires an exact command or URL string")

    def matches(self, settings: MCPServerSettings) -> bool:
        """Compare the effective executable or complete URL without normalization."""
        return settings.transport == self.transport and self.value == (
            settings.command if self.transport == "stdio" else settings.url
        )


ServerMatcher = MCPServerIdentity | MCPCommandMatcher | MCPUrlMatcher
ServerRules = tuple[tuple[str, ServerMatcher], ...]


def _freeze_rules(value: ServerRules | None) -> ServerRules | None:
    if value is None:
        return None
    result = tuple((name, identity) for name, identity in value)
    names = set()
    for name, identity in result:
        if (
            not isinstance(name, str)
            or name in names
            or not isinstance(identity, (MCPServerIdentity, MCPCommandMatcher, MCPUrlMatcher))
        ):
            raise ValueError("MCP requirements need unique names and typed identities")
        names.add(name)
    return result


def _parse_rules(value: object, *, path: str = "mcp_servers") -> ServerRules | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("MCP server requirements must be a table")
    result = []
    for name, rule in value.items():
        try:
            kind, fields = identity_fields(rule)
            if kind == "command":
                matcher = MCPCommandMatcher.from_mapping(fields)
            elif kind == "url":
                matcher = MCPUrlMatcher(MCPValueMatcher.from_mapping(fields))
            else:
                matcher = MCPServerIdentity(kind, fields)
            result.append((name, matcher))
        except ValueError as exc:
            raise ValueError(f"{path}[{name!r}]: {exc}") from exc
    return _freeze_rules(tuple(result))


@dataclass(frozen=True, slots=True)
class MCPRequirements:
    """Independent host restrictions, retained across user server replacements.

    None means no list; an empty server list denies every server including plugins.
    Plugin lists only restrict when at least one package specifies its MCP servers.
    """

    servers: ServerRules | None = None
    plugins: tuple[tuple[str, ServerRules | None], ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "servers", _freeze_rules(self.servers))
        if self.plugins is not None:
            packages = tuple((name, _freeze_rules(rules)) for name, rules in self.plugins)
            if any(not isinstance(name, str) for name, _ in packages) or len(dict(packages)) != len(
                packages
            ):
                raise ValueError("MCP plugin requirements need unique package names")
            object.__setattr__(self, "plugins", packages)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "MCPRequirements":
        """Parse only MCP authority; do not silently accept unenforced global permissions."""
        validate_requirements_shape(value)
        plugins = value.get("plugins")
        parsed = None
        if plugins is not None:
            parsed = []
            for name, rules in plugins.items():
                parsed.append(
                    (name, _parse_rules(rules.get("mcp_servers"), path=f"plugins[{name!r}]"))
                )
        return cls(
            _parse_rules(value.get("mcp_servers")), None if parsed is None else tuple(parsed)
        )

    @classmethod
    def coerce(cls, value: "MCPRequirements | Mapping[str, object] | None") -> "MCPRequirements":
        """Validate the host boundary before constructing any Runtime resources."""
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls.from_mapping(value)
        raise ValueError("MCP requirements must be a typed policy or mapping")

    def allows(self, settings: MCPServerSettings, source: MCPServerSource) -> bool:
        """Apply native global/plugin scope before transport startup or publication."""
        if source.plugin_id is None:
            rules = self.servers
        else:
            if self.servers == ():
                return False
            if self.plugins is None or not any(rules is not None for _, rules in self.plugins):
                return True
            rules = dict(self.plugins).get(source.plugin_id)
            if rules is None:
                return False
        if rules is None:
            return True
        identity = dict(rules).get(source.raw_name)
        return identity is not None and identity.matches(settings)
