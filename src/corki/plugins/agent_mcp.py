"""Strict Agent Plugin MCP declarations, separate from ordinary plugin config."""

import os
import re
import stat
from pathlib import Path

from corki.config import MCPServerSettings
from corki.config.mcp_url import agent_url
from corki.plugins.agent_overlay import apply_env_overlay
from corki.plugins.agent_paths import (
    contained_path,
    expand_paths,
    resolve_prefix,
    working_directory,
)
from corki.plugins.manifest_path import json_object
from corki.plugins.mcp import PluginMCPDeclarations
from corki.protocol.wire_json import check_fields, materialize, object_pairs

_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_HEADER = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~\-]+")
_CLIENT_HEADERS = frozenset(
    (
        "accept",
        "authorization",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "host",
        "last-event-id",
        "mcp-protocol-version",
        "mcp-session-id",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
    )
)


def load_agent_mcp(
    root: Path, overlay: str | None, *, data_root: Path | None = None
) -> PluginMCPDeclarations:
    """Malformed optional MCP sources disable MCP only; valid siblings survive."""
    source = root / "mcp.json"
    try:
        if not stat.S_ISREG(source.lstat().st_mode):
            raise ValueError("Agent Plugin MCP config must be a regular file")
        if not source.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            raise ValueError("Agent Plugin MCP config resolves outside the plugin root")
    except FileNotFoundError:
        return PluginMCPDeclarations((), ())
    except (OSError, ValueError, RuntimeError) as error:
        return PluginMCPDeclarations((), (str(error),))
    try:
        contents = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return PluginMCPDeclarations((), ())
    try:
        value = json_object(contents, value_object=False)
        check_fields(value, {"$schema", "mcpServers"})
        if set(value) != {"$schema", "mcpServers"} or value["$schema"] != _SCHEMA:
            raise ValueError("Agent Plugin MCP requires its supported schema and mcpServers only")
        if not isinstance(value["mcpServers"], dict):
            raise ValueError("Agent Plugin mcpServers must be an object")
        declarations = {}
        for name, config in object_pairs(value["mcpServers"]):
            name.encode("utf-8")
            declarations[name] = materialize(config, depth=3, preserve_pairs=False)
    except ValueError as error:
        return PluginMCPDeclarations((), (f"{source}: {error}",))
    servers, warnings = [], []
    for name, config in sorted(declarations.items()):
        try:
            servers.append(_server(name, config, root, data_root=data_root))
        except (ValueError, OSError, RuntimeError) as error:
            warnings.append(f"{name}: {error}")
    if data_root is not None and any(server.transport == "stdio" for server in servers):
        try:
            data_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            warnings.append(
                f"failed to create Agent Plugins data directory {data_root}; "
                f"disabling stdio MCP servers: {error}"
            )
            servers = [server for server in servers if server.transport != "stdio"]
    return apply_env_overlay(root, PluginMCPDeclarations(tuple(servers), tuple(warnings)), overlay)


def _server(
    name: str, value: object, root: Path, *, data_root: Path | None = None
) -> MCPServerSettings:
    if not isinstance(value, dict):
        raise ValueError("Agent Plugin MCP server must be an object")
    kind = value.get("type")
    if kind == "stdio":
        if set(value) - {"type", "command", "args", "env", "cwd"}:
            raise ValueError("unknown Agent Plugin stdio field")
        command = value.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError("Agent Plugin stdio command must be a string")
        bare = (
            "/" not in command
            and "\\" not in command
            and not (os.name == "nt" and Path(command).drive)
        )
        relative = command.startswith("./") and len(command) > 2 and "\\" not in command
        if not bare and not relative:
            raise ValueError("Agent Plugin command must be a bare executable or contained ./ path")
        args = value.get("args", [])
        if not isinstance(args, list) or not all(isinstance(v, str) for v in args):
            raise ValueError("Agent Plugin args must be a string array")
        env = _string_map(value.get("env", {}), "env")
        if os.name == "nt":
            upper = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            normalized = {k.translate(upper): v for k, v in env.items()}
            if len(normalized) != len(env):
                raise ValueError("duplicate case-insensitive Agent Plugin environment variable")
            env = normalized
        if {"PLUGIN_ROOT", "PLUGIN_DATA"} & env.keys():
            raise ValueError("Agent Plugin env cannot override reserved path variables")
        root, data = resolve_prefix(root), resolve_prefix(data_root or root / ".plugin-data")
        cwd = value.get("cwd", "${PLUGIN_ROOT}")
        if not isinstance(cwd, str):
            raise ValueError("Agent Plugin cwd must be a string when present")
        env = {k: expand_paths(v, root, data) for k, v in env.items()}
        env.update(PLUGIN_ROOT=str(root), PLUGIN_DATA=str(data))
        return MCPServerSettings(
            name=name,
            transport="stdio",
            command=str(contained_path(command, root)) if relative else command,
            args=tuple(expand_paths(v, root, data) for v in args),
            env=tuple(sorted(env.items())),
            cwd=working_directory(cwd, root, data),
        )
    if kind not in ("streamable-http", "sse") or set(value) - {"type", "url", "headers"}:
        raise ValueError("unsupported Agent Plugin transport or unknown HTTP field")
    raw_url = value.get("url")
    if not isinstance(raw_url, str):
        raise ValueError("Agent Plugin URL must be a string")
    headers = _string_map(value["headers"], "headers") if "headers" in value else {}
    if kind == "sse":
        raise ValueError("Agent Plugin legacy SSE transport is unsupported")
    agent_url(raw_url)
    seen = set()
    for key, val in headers.items():
        if key.lower() in seen or _HEADER.fullmatch(key) is None:
            raise ValueError("invalid or duplicate case-insensitive Agent Plugin HTTP header")
        seen.add(key.lower())
        if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in val):
            raise ValueError(f"invalid Agent Plugin HTTP header value for {key}")
    headers = {k: v for k, v in sorted(headers.items()) if k.lower() not in _CLIENT_HEADERS}
    return MCPServerSettings(
        name=name, transport="http", url=raw_url, http_headers=tuple(headers.items()) or None
    )


def _string_map(value: object, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError(f"Agent Plugin {field} must be a string object")
    return dict(value)
