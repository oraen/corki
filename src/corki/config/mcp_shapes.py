"""MCP wire-shape validation, intentionally separate from regex compilation.

Native managed layers deserialize each layer before merging, but compile only
effective patterns. These helpers never produce executable authority objects.
"""

from collections.abc import Mapping


def value_matcher_fields(value: object) -> tuple[str, str]:
    """Decode a strict tagged matcher without interpreting its expression."""
    if not isinstance(value, Mapping):
        raise ValueError("MCP value matcher must be an object")
    kind = value.get("match")
    if not isinstance(kind, str) or kind not in {"exact", "prefix", "regex"}:
        raise ValueError("invalid MCP value matcher")
    field = "expression" if kind == "regex" else "value"
    if set(value) != {"match", field}:
        raise ValueError("invalid MCP matcher fields")
    if not isinstance(value[field], str):
        raise ValueError("MCP matcher value must be a string")
    return kind, value[field]


def command_matcher_fields(value: object) -> tuple[str, tuple[object, ...]]:
    """Require the executable and complete positional argument matcher array."""
    if not isinstance(value, Mapping) or set(value) != {"executable", "args"}:
        raise ValueError("invalid MCP command matcher fields")
    if not isinstance(value["executable"], str):
        raise ValueError("MCP executable must be a string")
    if not isinstance(value["args"], (list, tuple)):
        raise ValueError("MCP command args must be an array")
    args = tuple(value["args"])
    for arg in args:
        value_matcher_fields(arg)
    return value["executable"], args


def identity_fields(rule: object) -> tuple[str, object]:
    """Preserve native legacy-first parsing, including ignored legacy siblings."""
    if not isinstance(rule, Mapping) or not isinstance(rule.get("identity"), Mapping):
        raise ValueError("MCP server requirement needs identity")
    identity = rule["identity"]
    if isinstance(identity.get("command"), str):
        return "stdio", identity["command"]
    if isinstance(identity.get("url"), str):
        return "http", identity["url"]
    if set(identity) == {"command"}:
        command_matcher_fields(identity["command"])
        return "command", identity["command"]
    if set(identity) == {"url"}:
        value_matcher_fields(identity["url"])
        return "url", identity["url"]
    raise ValueError("invalid typed MCP identity fields")


def validate_requirements_shape(value: Mapping[str, object]) -> None:
    """Reject invalid individual layers, including unenforced policy domains."""
    if value.keys() - {"mcp_servers", "plugins"}:
        raise ValueError("Unsupported non-MCP managed requirement")
    tables = [("mcp_servers", value.get("mcp_servers"))]
    plugins = value.get("plugins")
    if plugins is not None:
        if not isinstance(plugins, Mapping):
            raise ValueError("MCP plugin requirements must be a table")
        for name, rules in plugins.items():
            if not isinstance(name, str):
                raise ValueError("MCP plugin requirements need string package names")
            if not isinstance(rules, Mapping) or rules.keys() - {"mcp_servers"}:
                raise ValueError("Unsupported plugin requirement; only mcp_servers is implemented")
            tables.append((f"plugins[{name!r}].mcp_servers", rules.get("mcp_servers")))
    for path, rules in tables:
        if rules is None:
            continue
        if not isinstance(rules, Mapping):
            raise ValueError(f"{path}: MCP server requirements must be a table")
        for name, rule in rules.items():
            if not isinstance(name, str):
                raise ValueError(f"{path}: MCP requirements need string server names")
            try:
                identity_fields(rule)
            except ValueError as exc:
                raise ValueError(f"{path}[{name!r}]: {exc}") from exc
