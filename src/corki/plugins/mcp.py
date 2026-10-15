"""Ordinary host plugin MCP sources; not Agent Plugin or executor path policy."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corki.config import MCPServerSettings


@dataclass(frozen=True, slots=True)
class PluginMCPDeclarations:
    """Valid siblings survive optional MCP source failures, with host diagnostics."""

    servers: tuple[MCPServerSettings, ...]
    warnings: tuple[str, ...]


def load_plugin_mcp(
    root: Path, document: Mapping[str, Any], plugin: Mapping[str, Any]
) -> PluginMCPDeclarations:
    """Normalize before shared transport validation; never invent a default cwd."""
    declarations, warnings = [], []
    section = document.get("mcp")
    if section is not None:
        if isinstance(section, Mapping):
            declarations.append((section.get("servers"), False))
        else:
            warnings.append("mcp section must be a table")

    configured = plugin.get("mcpServers")
    if isinstance(configured, Mapping):
        declarations.append((configured, True))
    else:
        source = _manifest_path(root, configured, warnings)
        if source is None:
            default = root / ".mcp.json"
            source = default if default.is_file() else None
        try:
            contents = source.read_text(encoding="utf-8") if source is not None else None
        except (OSError, UnicodeError):
            # Native discovery treats unavailable optional files as absent.
            pass
        else:
            if contents is not None:
                try:
                    declarations.append((json.loads(contents, parse_constant=_invalid_json), True))
                except ValueError:
                    warnings.append(f"{source}: invalid MCP JSON document")

    servers: dict[str, MCPServerSettings] = {}
    for declaration, wrapped in declarations:
        if declaration is None and not wrapped:
            continue
        if not isinstance(declaration, Mapping):
            warnings.append("MCP declarations must be a server object")
            continue
        if wrapped and isinstance(declaration.get("mcpServers"), Mapping):
            declaration = declaration["mcpServers"]
        for name, value in sorted(declaration.items()):
            try:
                if not isinstance(name, str) or not name.strip() or not isinstance(value, Mapping):
                    raise ValueError("MCP server must be a named object")
                normalized = dict(value)
                kind = normalized.pop("type", None)
                if isinstance(kind, str) and kind not in (
                    "stdio",
                    "http",
                    "streamable_http",
                    "streamable-http",
                ):
                    warnings.append(f"{name}: unknown plugin MCP transport type {kind!r}")
                cwd = normalized.get("cwd")
                if isinstance(cwd, str) and not Path(cwd).is_absolute():
                    # Ordinary host plugins use lexical joining, not shell tilde
                    # expansion or Agent Plugin package-containment semantics.
                    normalized["cwd"] = str(root / cwd)
                server = MCPServerSettings.from_mapping(name, normalized)
            except (TypeError, ValueError) as error:
                warnings.append(f"{name}: {error}")
            else:
                servers.setdefault(name, server)
    return PluginMCPDeclarations(tuple(servers.values()), tuple(warnings))


def _manifest_path(root: Path, value: object, warnings: list[str]) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        warnings.append("ignoring mcpServers: expected a string or object")
        return None
    if not value.startswith("./") or value == "./":
        warnings.append("ignoring mcpServers: path must start with ./ and name a file")
        return None
    relative = value[2:]
    parts = relative.replace("\\", "/").split("/") if os.name == "nt" else relative.split("/")
    if ".." in parts or Path(relative).anchor:
        warnings.append("ignoring mcpServers: path must stay within the plugin root")
        return None
    # Ordinary plugins permit symlinked files. Do not resolve/canonicalize here;
    # Agent Plugin regular-file and resolved-containment checks are a different contract.
    return root / relative


def _invalid_json(value: str) -> None:
    raise ValueError("non-JSON numeric constant")
