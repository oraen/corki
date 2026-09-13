"""Model-visible inventory of loaded plugins and their contributions."""

from __future__ import annotations

from pathlib import Path

from corki.plugins.mentions import explicit_plugin_ids
from corki.plugins.models import LoadedPlugin
from corki.prompting import PromptContribution, PromptPhase, PromptRole, PromptSlot
from corki.protocol.tools import ToolExposure

_TRUNCATED = "\n- Additional plugin capabilities omitted to fit the context limit."


class PluginContextContributor:
    def __init__(
        self,
        plugins: tuple[LoadedPlugin, ...],
        warnings: tuple[str, ...] = (),
        *,
        allow_input_mentions: bool = True,
    ) -> None:
        self._plugins = plugins
        self._warnings = warnings
        self._allow_input_mentions = allow_input_mentions

    def with_plugins(self, plugins, warnings=()):
        return PluginContextContributor(
            plugins, warnings, allow_input_mentions=self._allow_input_mentions
        )

    def input_contributions(self, *, cwd: Path, user_input: str):
        return self.input_snapshot_contributions(
            cwd=cwd, user_input=user_input, mentions=(), tool_snapshot=None
        )

    def input_snapshot_contributions(self, *, cwd: Path, user_input: str, mentions, tool_snapshot):
        del cwd
        if not self._allow_input_mentions:
            return ()
        selected = explicit_plugin_ids(user_input, mentions)
        available = (
            {
                (tool_snapshot.mcp_plugin_id(spec.name), server)
                for spec in tool_snapshot.specs()
                if spec.exposure is not ToolExposure.HIDDEN
                and (server := tool_snapshot.mcp_server_name(spec.name)) is not None
            }
            if tool_snapshot is not None
            else set()
        )
        fragments = []
        for index, plugin in enumerate(self._plugins):
            manifest = plugin.manifest
            if (
                not manifest.enabled
                or manifest.error is not None
                or manifest.identity not in selected
            ):
                continue
            lines = [f"Capabilities from the `{manifest.identity}` plugin:"]
            if manifest.skills_path is not None:
                lines.append(f"- Skills from this plugin are prefixed with `{manifest.name}:`.")
            servers = sorted(
                {server for plugin_id, server in available if plugin_id == manifest.identity}
            )
            if servers:
                lines.append(
                    "- MCP servers from this plugin available in this session: "
                    + ", ".join(f"`{server}`" for server in servers)
                    + "."
                )
            if len(lines) == 1:
                continue
            lines.append("Use these plugin-associated capabilities to help solve the task.")
            contents = "\n".join(lines)
            if len(contents.encode("utf-8")) > 4096:
                contents = (
                    contents.encode("utf-8")[: 4096 - len(_TRUNCATED.encode("utf-8"))].decode(
                        "utf-8", errors="ignore"
                    )
                    + _TRUNCATED
                )
            fragments.append(
                PromptContribution(
                    key=f"extensions.plugins.selected.{manifest.identity}",
                    content_kind="plugins.instructions",
                    template_name="extensions/plugins/selected",
                    role=PromptRole.DEVELOPER,
                    slot=PromptSlot.EXTENSIONS,
                    order=200 + index,
                    variables={"contents": contents},
                    separate_message=True,
                    input_scoped=True,
                )
            )
        return tuple(fragments)

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
            if not manifest.enabled or manifest.error is not None:
                continue
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
            version = f" {manifest.version}" if manifest.version else ""
            lines.append(f"- {manifest.identity}{version}: {manifest.description or ''}{suffix}")
        lines.extend(f"- warning: {warning}" for warning in self._warnings)
        guidance = (
            (
                PromptContribution(
                    key="extensions.plugins.guidance",
                    template_name="extensions/plugins/guidance",
                    role=PromptRole.DEVELOPER,
                    slot=PromptSlot.EXTENSIONS,
                    order=49,
                    phase=PromptPhase.WORLD_STATE,
                    content_kind="plugins.usage_instructions",
                    snapshot_state="plugins.available",
                ),
            )
            if any(p.manifest.enabled and p.manifest.error is None for p in self._plugins)
            else ()
        )
        return (
            *guidance,
            PromptContribution(
                key="extensions.plugins.catalog",
                phase=PromptPhase.WORLD_STATE,
                content_kind="corki.plugins.catalog",
                template_name="extensions/plugins/catalog",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                order=50,
                variables={"plugins": "\n".join(lines)},
            ),
        )
