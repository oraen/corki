"""Validated configuration values for optional runtime capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class MCPServerSettings:
    """One MCP connection declared by the user or a plugin."""

    name: str
    transport: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    cwd: Path | None = None
    url: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 30.0
    tool_output_token_limits: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        limits = tuple(tuple(pair) for pair in self.tool_output_token_limits)
        names = set()
        for name, limit in limits:
            if not isinstance(name, str) or not name or name in names:
                raise ValueError("MCP output token limits require unique tool names")
            if type(limit) is not int or limit <= 0:
                raise ValueError("MCP output_token_limit must be a positive integer")
            names.add(name)
        object.__setattr__(self, "tool_output_token_limits", limits)

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any]) -> MCPServerSettings:
        # Codex plugin manifests use ``type`` while Corki's TOML examples use
        # ``transport``. Accept both so copied or generated plugins are usable.
        transport = str(value.get("transport") or value.get("type") or "stdio").strip().lower()
        if transport not in {"stdio", "http"}:
            raise ValueError(f"mcp.servers.{name}.transport must be stdio or http")
        command = value.get("command")
        url = value.get("url")
        if transport == "stdio" and (not isinstance(command, str) or not command.strip()):
            raise ValueError(f"mcp.servers.{name}.command is required for stdio")
        if transport == "http" and (
            not isinstance(url, str) or not url.startswith(("http://", "https://"))
        ):
            raise ValueError(f"mcp.servers.{name}.url must be an http(s) URL")
        args = value.get("args", [])
        env = value.get("env", {})
        headers = value.get("headers", {})
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError(f"mcp.servers.{name}.args must be an array of strings")
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in env.items()
        ):
            raise ValueError(f"mcp.servers.{name}.env must be a string table")
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in headers.items()
        ):
            raise ValueError(f"mcp.servers.{name}.headers must be a string table")
        timeout = value.get("timeout_seconds", 30.0)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError(f"mcp.servers.{name}.timeout_seconds must be positive")
        cwd = value.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise ValueError(f"mcp.servers.{name}.cwd must be a path string")
        tools = value.get("tools", {})
        if not isinstance(tools, Mapping):
            raise ValueError(f"mcp.servers.{name}.tools must be a table")
        limits = []
        for tool_name, config in tools.items():
            if not isinstance(config, Mapping):
                raise ValueError("MCP per-tool settings must be a table")
            if config.get("output_token_limit") is not None:
                limits.append((tool_name, config["output_token_limit"]))
        return cls(
            name=name,
            transport=transport,
            command=command.strip() if isinstance(command, str) else None,
            args=tuple(args),
            env=tuple(env.items()),
            cwd=Path(cwd).expanduser() if cwd else None,
            url=url.strip() if isinstance(url, str) else None,
            headers=tuple(headers.items()),
            timeout_seconds=float(timeout),
            tool_output_token_limits=tuple(limits),
        )


def parse_mcp_servers(value: object) -> tuple[MCPServerSettings, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ValueError("mcp.servers must be a TOML table")
    servers = []
    for name, server in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(server, dict):
            raise ValueError("each mcp.servers entry must be a named TOML table")
        if server.get("enabled", True):
            servers.append(MCPServerSettings.from_mapping(name, server))
    return tuple(servers)
