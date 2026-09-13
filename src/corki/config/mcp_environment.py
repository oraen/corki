"""Validated MCP variable references, preserving string versus structured config."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MCPEnvVar:
    """A structured reference; remote sources require an actual remote launcher."""

    name: str
    source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError("MCP env_vars name must be a string")
        if self.source not in (None, "local", "remote"):
            raise ValueError("MCP env_vars source must be local or remote")


def parse_env_vars(value: object) -> tuple[str | MCPEnvVar, ...]:
    """Normalize optional raw arrays without trimming names or losing source variants."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("MCP env_vars must be an array")
    result = []
    for entry in value:
        if isinstance(entry, (str, MCPEnvVar)):
            result.append(entry)
        elif isinstance(entry, Mapping) and set(entry) <= {"name", "source"} and "name" in entry:
            result.append(MCPEnvVar(entry["name"], entry.get("source")))
        else:
            raise ValueError("MCP env_vars entries must be strings or {name, source?} records")
    return tuple(result)
