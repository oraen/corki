"""Legacy overlays cannot replace an Agent Plugin's identity or MCP declarations."""

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from corki.config.mcp_environment import MCPEnvVar
from corki.plugins.agent_hooks import validate_hook_metadata
from corki.plugins.manifest_path import json_object
from corki.plugins.mcp import PluginMCPDeclarations, load_plugin_mcp
from corki.protocol.wire_json import NUMBER_KEY, check_fields, materialize, object_pairs
from corki.protocol.wire_numbers import WireNumber


def parse_overlay(contents: str) -> dict[str, Any]:
    """Validate legacy metadata that can reject even a presentation-only overlay."""
    value = json_object(contents, value_object=False)
    check_fields(
        value,
        {
            "name",
            "version",
            "description",
            "keywords",
            "skills",
            "mcpServers",
            "apps",
            "hooks",
            "interface",
        },
    )
    if "name" in value and not isinstance(value["name"], str):
        raise ValueError("overlay name must be a string")
    if "name" in value:
        value["name"].encode("utf-8")
    for field in ("version", "description", "apps"):
        if value.get(field) is not None and not isinstance(value[field], str):
            raise ValueError(f"overlay {field} must be a string")
        if isinstance(value.get(field), str):
            value[field].encode("utf-8")
    _string_list(value, "keywords")
    # Untagged fields buffer their contents before selecting a variant. Unlike
    # ignored top-level extensions, even unused strings here must decode.
    for field in ("skills", "hooks", "mcpServers"):
        if value.get(field) is not None:
            materialize(value[field])
    if value.get("skills") is not None:
        materialize(value["skills"], preserve_pairs=False)
    if value.get("hooks") is not None:
        validate_hook_metadata(value["hooks"])
    configured = value.get("mcpServers")
    if isinstance(configured, WireNumber):
        # Raw serde_json -> Content uses i64/u64 only for in-range integer
        # syntax. All other numbers visit a private map, so the untagged MCP
        # Object branch wins and must NOT fall back to the default .mcp.json.
        token = configured.token
        scalar_integer = (
            len(token) <= 20
            and not any(char in token for char in ".e")
            and -(2**63) <= int(token) < 2**64
        )
        if not scalar_integer:
            configured = {NUMBER_KEY: token}
    if isinstance(configured, dict):
        servers = {}
        for name, server in object_pairs(configured):
            name.encode("utf-8")
            servers[name] = materialize(server, preserve_pairs=False)
        value["mcpServers"] = servers
    elif configured is not None:
        # An invalid path/object shape still has to deserialize Invalid(Value).
        materialize(configured, preserve_pairs=False)
    interface = value.get("interface")
    if interface is not None:
        if not isinstance(interface, dict):
            raise ValueError("overlay interface must be an object")
        fields = (
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "websiteUrl",
            "privacyPolicyUrl",
            "termsOfServiceUrl",
            "brandColor",
            "composerIcon",
            "logo",
            "logoDark",
            "websiteURL",
            "privacyPolicyURL",
            "termsOfServiceURL",
        )
        check_fields(interface, {*fields, "capabilities", "screenshots", "defaultPrompt"})
        for field in fields:
            if interface.get(field) is not None and not isinstance(interface[field], str):
                raise ValueError(f"overlay interface.{field} must be a string")
            if isinstance(interface.get(field), str):
                interface[field].encode("utf-8")
        for field in ("website", "privacyPolicy", "termsOfService"):
            if field + "Url" in interface and field + "URL" in interface:
                raise ValueError(f"duplicate overlay interface field {field}Url")
        for field in ("capabilities", "screenshots"):
            _string_list(interface, field)
        if interface.get("defaultPrompt") is not None:
            materialize(interface["defaultPrompt"], preserve_pairs=False)
    return value


def apply_env_overlay(
    root: Path, outcome: PluginMCPDeclarations, contents: str | None
) -> PluginMCPDeclarations:
    """Append only local references to matching stdio servers, never server authority."""
    if not outcome.servers or contents is None:
        return outcome
    try:
        overlay = parse_overlay(contents)
    except ValueError as error:
        return replace(outcome, warnings=(*outcome.warnings, f"invalid MCP overlay: {error}"))
    # Only native mcpServers/default-file sources, not Corki's extended mcp table.
    declarations = load_plugin_mcp(root, {}, overlay)
    by_name = {server.name: server for server in declarations.servers}
    servers = []
    for server in outcome.servers:
        other = by_name.get(server.name)
        if server.transport == "stdio" and other is not None and other.transport == "stdio":
            variables = tuple(
                v for v in other.env_vars if not isinstance(v, MCPEnvVar) or v.source != "remote"
            )
            names = {_env_name(v.name if isinstance(v, MCPEnvVar) else v) for v in variables}
            env = tuple(
                (key, value)
                for key, value in server.env
                if not (
                    value.startswith("${")
                    and value.endswith("}")
                    and _env_name(key) in names
                    and _env_name(value[2:-1]) == _env_name(key)
                )
            )
            server = replace(server, env=env, env_vars=(*server.env_vars, *variables))
        servers.append(server)
    return PluginMCPDeclarations(tuple(servers), (*outcome.warnings, *declarations.warnings))


def _env_name(value: str) -> str:
    return (
        value.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        if os.name == "nt"
        else value
    )


def _string_list(value: dict[str, Any], field: str) -> None:
    if field in value and (
        not isinstance(value[field], list) or not all(isinstance(v, str) for v in value[field])
    ):
        raise ValueError(f"overlay {field} must be a string array")
    for item in value.get(field, []):
        item.encode("utf-8")
