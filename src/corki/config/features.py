"""Validated configuration values for optional runtime capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corki.config.mcp_environment import MCPEnvVar, parse_env_vars
from corki.config.mcp_headers import RUST_WHITESPACE, header_pairs
from corki.config.mcp_oauth import MCPServerOAuthSettings


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
    enabled_tools: tuple[str, ...] | None = None
    disabled_tools: tuple[str, ...] | None = None
    env_vars: tuple[str | MCPEnvVar, ...] = ()
    http_headers: tuple[tuple[str, str], ...] | None = None
    env_http_headers: tuple[tuple[str, str], ...] | None = None
    bearer_token_env_var: str | None = None
    http_headers_helper: str | None = None
    default_tools_approval_mode: str | None = None
    tool_approval_modes: tuple[tuple[str, str], ...] = ()
    enabled: bool = True
    disabled_reason: str | None = None
    environment_id: str = "local"
    omit_tools_from: tuple[str, ...] | None = None
    required: bool = False
    scopes: tuple[str, ...] | None = None
    oauth: MCPServerOAuthSettings | None = None
    oauth_resource: str | None = None

    def __post_init__(self) -> None:
        if self.oauth is not None:
            if self.transport != "http":
                raise ValueError("MCP oauth is only supported for http")
            if not isinstance(self.oauth, MCPServerOAuthSettings):
                object.__setattr__(self, "oauth", MCPServerOAuthSettings.from_mapping(self.oauth))
        if self.oauth_resource is not None:
            if not isinstance(self.oauth_resource, str):
                raise ValueError("MCP oauth_resource must be a string")
            if self.transport != "http":
                raise ValueError("MCP oauth_resource is only supported for http")
        if not isinstance(self.required, bool):
            raise ValueError("MCP required must be a boolean")
        omitted = self.omit_tools_from
        if omitted is not None:
            if not isinstance(omitted, (list, tuple)) or not all(
                isinstance(value, str) and value in {"direct", "deferred", "code_mode"}
                for value in omitted
            ):
                raise ValueError(
                    "MCP omit_tools_from must be an array of direct/deferred/code_mode"
                )
            object.__setattr__(self, "omit_tools_from", tuple(omitted))
        if not isinstance(self.environment_id, str):
            raise ValueError("MCP environment_id must be a string")
        if not isinstance(self.enabled, bool):
            raise ValueError("MCP enabled must be a boolean")
        if self.disabled_reason is not None and not isinstance(self.disabled_reason, str):
            raise ValueError("MCP disabled reason must be a string")
        modes = ("auto", "prompt", "writes", "approve")
        if (
            self.default_tools_approval_mode is not None
            and self.default_tools_approval_mode not in modes
        ):
            raise ValueError("MCP default_tools_approval_mode is invalid")
        overrides = tuple(tuple(pair) for pair in self.tool_approval_modes)
        names = set()
        for name, mode in overrides:
            if not isinstance(name, str) or name in names or mode not in modes:
                raise ValueError("MCP tool approval_mode requires unique names and a valid mode")
            names.add(name)
        object.__setattr__(self, "tool_approval_modes", overrides)
        if self.http_headers_helper is not None:
            if not isinstance(self.http_headers_helper, str) or not self.http_headers_helper.strip(
                RUST_WHITESPACE
            ):
                raise ValueError("MCP http_headers_helper must be a nonempty command string")
            if self.transport != "http":
                raise ValueError("MCP http_headers_helper is only supported for http")
            if self.environment_id != "local":
                raise ValueError("MCP HTTP headers helpers require the local environment")
        for key in ("http_headers", "env_http_headers"):
            value = header_pairs(getattr(self, key), key)
            if self.transport != "http" and value is not None:
                raise ValueError(f"MCP {key} is only supported for http")
            object.__setattr__(self, key, value)
        if self.bearer_token_env_var is not None:
            if not isinstance(self.bearer_token_env_var, str):
                raise ValueError("MCP bearer_token_env_var must be a string")
            if self.transport != "http":
                raise ValueError("MCP bearer_token_env_var is only supported for http")
        if self.http_headers is not None and self.headers:
            raise ValueError("MCP headers and http_headers cannot both be set")
        object.__setattr__(self, "env_vars", parse_env_vars(self.env_vars))
        if self.transport != "stdio" and self.env_vars:
            raise ValueError("MCP env_vars is only supported for stdio")
        for key in ("enabled_tools", "disabled_tools", "scopes"):
            value = getattr(self, key)
            if value is not None:
                if not isinstance(value, (list, tuple)) or not all(
                    isinstance(name, str) for name in value
                ):
                    raise ValueError(f"MCP {key} must be an array of strings")
                object.__setattr__(self, key, tuple(value))
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
        inferred = (
            "http" if value.get("url") is not None and value.get("command") is None else "stdio"
        )
        transport = str(value.get("transport") or value.get("type") or inferred).strip().lower()
        if transport not in {"stdio", "http"}:
            raise ValueError(f"mcp.servers.{name}.transport must be stdio or http")
        environment_id = value.get("environment_id")
        if environment_id is None:
            environment_id = "local"
        if not isinstance(environment_id, str):
            raise ValueError(f"mcp.servers.{name}.environment_id must be a string")
        if value.get("http_headers_helper") is not None and environment_id != "local":
            raise ValueError("MCP HTTP headers helpers require the local environment")
        if transport != "stdio" and value.get("env_vars") is not None:
            raise ValueError("MCP env_vars is only supported for stdio")
        excluded = ("args", "env", "cwd", "command") if transport == "http" else ("url", "headers")
        for field in excluded:
            if value.get(field) is not None:
                raise ValueError(f"MCP {field} is not supported for {transport}")
        if value.get("bearer_token") is not None:
            raise ValueError("MCP bearer_token is unsupported; use bearer_token_env_var")
        if value.get("headers") is not None and value.get("http_headers") is not None:
            raise ValueError("MCP headers and http_headers cannot both be set")
        for key in ("http_headers", "env_http_headers"):
            if value.get(key) is not None and not isinstance(value[key], Mapping):
                raise ValueError(f"MCP {key} must be a string table")
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
        approval_modes = []
        for tool_name, config in tools.items():
            if not isinstance(config, Mapping):
                raise ValueError("MCP per-tool settings must be a table")
            if config.get("output_token_limit") is not None:
                limits.append((tool_name, config["output_token_limit"]))
            if config.get("approval_mode") is not None:
                approval_modes.append((tool_name, config["approval_mode"]))
        return cls(
            name=name,
            transport=transport,
            command=command.strip() if isinstance(command, str) else None,
            args=tuple(args),
            env=tuple(env.items()),
            cwd=(Path(cwd).expanduser() if environment_id == "local" else Path(cwd))
            if cwd
            else None,
            url=url.strip() if isinstance(url, str) else None,
            headers=tuple(headers.items()),
            timeout_seconds=float(timeout),
            tool_output_token_limits=tuple(limits),
            enabled_tools=value.get("enabled_tools"),
            disabled_tools=value.get("disabled_tools"),
            omit_tools_from=value.get("omit_tools_from"),
            env_vars=value.get("env_vars", ()),
            http_headers=value.get("http_headers"),
            env_http_headers=value.get("env_http_headers"),
            bearer_token_env_var=value.get("bearer_token_env_var"),
            http_headers_helper=value.get("http_headers_helper"),
            default_tools_approval_mode=value.get("default_tools_approval_mode"),
            tool_approval_modes=tuple(approval_modes),
            enabled=value.get("enabled", True),
            required=value.get("required", False),
            environment_id=environment_id,
            scopes=value.get("scopes"),
            oauth=value.get("oauth"),
            oauth_resource=value.get("oauth_resource"),
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
        servers.append(MCPServerSettings.from_mapping(name, server))
    return tuple(servers)
