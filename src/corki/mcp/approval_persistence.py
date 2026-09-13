"""MCP consent writes use captured config sources and reload only user layers."""

import asyncio
import inspect
import logging
import tomllib
from dataclasses import replace
from pathlib import Path

from corki.config.features import parse_mcp_servers
from corki.config.layers import ConfigLayer, LocalConfigState, _merge
from corki.config.plugin_policies import raw_plugin_policies
from corki.config.shell_environment import parse_shell_environment_policy
from corki.config.toml_edits import set_config_value
from corki.mcp.catalog import MCPCatalogSource

_LOG = logging.getLogger(__name__)


def policy_document(configuration: LocalConfigState) -> dict:
    """Project layers stay admission-time snapshots, including after a user reload."""
    result = {}
    for layer in configuration.layers:
        if layer.disabled_reason is not None:
            continue
        document = tomllib.loads(layer.contents)
        _merge(result, {key: document[key] for key in ("mcp", "plugins") if key in document})
    parse_mcp_servers(result.get("mcp", {}).get("servers"))
    if "plugins" in result:
        result["plugins"] = raw_plugin_policies(result)
    return result


def approval_settings(settings, document, source: MCPCatalogSource | None, *, plugin_only=False):
    """Materialize policy once; later host reconciliation owns its new settings."""
    if plugin_only and (source is None or source.kind not in {"plugin", "selected_plugin"}):
        return settings
    server = settings.name
    plugin_policy = source is not None and source.kind in {"plugin", "selected_plugin"}
    if plugin_policy:
        config = (
            document.get("plugins", {}).get(source.identity, {}).get("mcp_servers", {}).get(server)
        )
    else:
        config = document.get("mcp", {}).get("servers", {}).get(server)
    if not isinstance(config, dict):
        return settings
    declared = settings.default_tools_approval_mode or "auto"
    default = config.get("default_tools_approval_mode")
    modes = dict(settings.tool_approval_modes) if plugin_policy else {}
    entries = config.get("tools", {})
    selected = source is not None and source.kind == "selected_plugin"
    fields = _plugin_policy_fields(settings, config, selected) if plugin_policy else {}
    if selected:
        for name, _ in settings.tool_output_token_limits:
            modes.setdefault(name, declared)
        for name, entry in entries.items():
            if (
                entry.get("approval_mode") is not None
                or entry.get("output_token_limit") is not None
            ):
                modes.setdefault(name, declared)
        for name, mode in tuple(modes.items()):
            requested = entries.get(name, {}).get("approval_mode") or default
            if requested is not None:
                modes[name] = restrict_mode(mode, requested)
        if default is not None:
            default = restrict_mode(declared, default)
    else:
        modes.update(
            (name, tool["approval_mode"])
            for name, tool in entries.items()
            if tool.get("approval_mode") is not None
        )
    return replace(
        settings,
        default_tools_approval_mode=default
        if default is not None or not plugin_policy
        else settings.default_tools_approval_mode,
        tool_approval_modes=tuple(modes.items()),
        **fields,
    )


def _plugin_policy_fields(settings, config, selected):
    """Installed overrides differ from selected roots' non-widening intersection."""
    enabled = config.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("plugin MCP enabled must be boolean")
    filters = {}
    for key in ("enabled_tools", "disabled_tools"):
        declared = getattr(settings, key)
        requested = config.get(key)
        if requested is not None:
            if not isinstance(requested, (list, tuple)) or any(
                not isinstance(name, str) for name in requested
            ):
                raise ValueError(f"plugin MCP {key} must be an array of names")
            if selected and declared is not None:
                filters[key] = (
                    tuple(name for name in declared if name in requested)
                    if key == "enabled_tools"
                    else tuple(dict.fromkeys((*declared, *requested)))
                )
            else:
                filters[key] = tuple(requested)
    limits = dict(settings.tool_output_token_limits)
    entries = config.get("tools", {})
    if not isinstance(entries, dict):
        raise ValueError("plugin MCP tools must be a table")
    for name, entry in entries.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise ValueError("plugin MCP tool policy must be a named table")
        limit = entry.get("output_token_limit")
        if limit is not None:
            if type(limit) is not int or limit <= 0:
                raise ValueError("plugin MCP output_token_limit must be a positive integer")
            limits[name] = min(limits.get(name, limit), limit)
    return {
        "enabled": settings.enabled and enabled if selected else enabled,
        "tool_output_token_limits": tuple(limits.items()),
        **filters,
    }


def restrict_mode(declared, requested):
    """Native Auto/Writes are incomparable; their intersection always prompts."""
    if declared not in {"auto", "writes", "prompt", "approve"} or requested not in {
        "auto",
        "writes",
        "prompt",
        "approve",
    }:
        raise ValueError("invalid MCP approval override")
    if declared == "prompt" or requested == "prompt":
        return "prompt"
    if declared == "approve":
        return requested
    if requested == "approve" or declared == requested:
        return declared
    return "prompt"


class MCPApprovalPersistence:
    def __init__(self, configuration: LocalConfigState, home: Path):
        if not isinstance(configuration, LocalConfigState):
            raise ValueError("MCP approval persistence needs host-owned config sources")
        if not any(layer.kind == "user" for layer in configuration.layers):
            configuration = replace(
                configuration,
                layers=(
                    ConfigLayer(home.absolute() / "config.toml", "user"),
                    *configuration.layers,
                ),
            )
        self.configuration = configuration
        self.document = policy_document(configuration)
        # A preparation callback returns a synchronous, non-fallible publisher.
        # All participants must finish validation before any state is replaced.
        self.prepare_reload = None
        self._lock = asyncio.Lock()

    def target(self, key, configuration, source):
        server, _, _, tool = key
        user = next(layer.file for layer in reversed(configuration.layers) if layer.kind == "user")
        for kind in ("project", "user"):
            for layer in reversed(configuration.layers):
                if layer.kind != kind or layer.disabled_reason is not None:
                    continue
                servers = parse_mcp_servers(
                    tomllib.loads(layer.contents).get("mcp", {}).get("servers")
                )
                if any(settings.name == server for settings in servers):
                    return layer.file, ("mcp", "servers", server, "tools", tool, "approval_mode")
        if source is not None and source.kind == "plugin":
            return user, (
                "plugins",
                source.identity,
                "mcp_servers",
                server,
                "tools",
                tool,
                "approval_mode",
            )
        raise ValueError("MCP server has no configured persistence destination")

    async def persist(self, key, configuration, source) -> None:
        # The caller retains revision authority until this owned task is joined.
        writer = asyncio.create_task(
            self._persist(key, configuration, source), name="mcp-approval-write"
        )
        cancelled = False
        while not writer.done():
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                cancelled = True
        writer.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _persist(self, key, configuration, source):
        async with self._lock:
            try:
                path, segments = self.target(key, configuration, source)
                await asyncio.to_thread(set_config_value, path, segments, "approve")
            except Exception as error:
                _LOG.warning(
                    "Failed to persist MCP tool approval: %s; using session consent",
                    type(error).__name__,
                )
                return
            try:
                layers = []
                for layer in self.configuration.layers:
                    if layer.kind == "user":
                        try:
                            contents = await asyncio.to_thread(
                                layer.file.read_text, encoding="utf-8"
                            )
                        except FileNotFoundError:
                            contents = ""
                        document = tomllib.loads(contents)
                        if "shell_environment_policy" in document:
                            parse_shell_environment_policy(document["shell_environment_policy"])
                        layer = replace(layer, contents=contents)
                    layers.append(layer)
                updated = replace(self.configuration, layers=tuple(layers))
                document = policy_document(updated)
                publish = (
                    self.prepare_reload(updated, document)
                    if self.prepare_reload is not None
                    else None
                )
                if inspect.isawaitable(publish):
                    publish = await publish
                if publish is not None:
                    publish()
                self.configuration, self.document = updated, document
            except Exception as error:
                _LOG.warning(
                    "Failed to reload MCP approval configuration: %s", type(error).__name__
                )
