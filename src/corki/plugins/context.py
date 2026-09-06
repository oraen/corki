"""Model-visible inventory of loaded plugins and their contributions."""

from __future__ import annotations

from pathlib import Path

from corki.plugins.models import LoadedPlugin
from corki.prompting import PromptContribution, PromptRole, PromptSlot


class PluginContextContributor:
    def __init__(
        self,
        plugins: tuple[LoadedPlugin, ...],
        warnings: tuple[str, ...] = (),
    ) -> None:
        self._plugins = plugins
        self._warnings = warnings

    def contributions(
        self,
        *,
        cwd: Path,
        user_input: str,
        realtime_active: bool,
    ) -> tuple[PromptContribution, ...]:
        del cwd, user_input, realtime_active
        if not self._plugins and not self._warnings:
            return ()
        lines = []
        for plugin in self._plugins:
            manifest = plugin.manifest
            capabilities = []
            if plugin.tool_names:
                capabilities.append("tools: " + ", ".join(plugin.tool_names))
            if manifest.skills_path is not None:
                capabilities.append(f"skills namespace: {manifest.name}")
            if manifest.mcp_servers:
                capabilities.append(
                    "MCP: " + ", ".join(server.name for server in manifest.mcp_servers)
                )
            suffix = f" ({'; '.join(capabilities)})" if capabilities else ""
            lines.append(f"- {manifest.name} {manifest.version}: {manifest.description}{suffix}")
        lines.extend(f"- warning: {warning}" for warning in self._warnings)
        return (
            PromptContribution(
                key="extensions.plugins.catalog",
                template_name="extensions/plugins/catalog",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                order=50,
                variables={"plugins": "\n".join(lines)},
            ),
        )
