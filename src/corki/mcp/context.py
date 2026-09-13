"""Model-visible MCP server inventory and startup diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from corki.prompting import PromptContribution, PromptRole, PromptSlot

if TYPE_CHECKING:
    from corki.mcp.manager import MCPManager


class MCPContextContributor:
    def __init__(self, manager: MCPManager) -> None:
        self._manager = manager

    def contributions(self, *, cwd: Path, user_input: str, realtime_active: bool):
        del cwd, user_input, realtime_active
        return self._contributions(self._manager.prompt_inventory)

    def step_contributions(self, *, tool_specs):
        names = set(self._manager.tool_names)
        return self._contributions(self._inventory(s for s in tool_specs if s.name in names))

    def snapshot_contributions(self, *, tool_snapshot):
        aggregate = self._manager._aggregate_tools
        specs = (
            spec
            for spec in tool_snapshot.specs()
            if tool_snapshot.mcp_server_name(spec.name) is not None
            or any(tool_snapshot.get(spec.name) is tool for tool in aggregate)
        )
        return self._contributions(self._inventory(specs))

    @staticmethod
    def _inventory(specs):
        specs = tuple(specs)
        names = [f"- {s.name}" for s in specs if s.exposure.is_model_visible]
        sources = sorted({s.source for s in specs if s.exposure.is_deferred and s.source})
        if sources:
            names.extend(
                (
                    "Deferred sources: " + ", ".join(sources),
                    "Use tool_search to discover their tools and load callable definitions.",
                )
            )
        return "\n".join(names)[:8_000] or "- none"

    def _contributions(self, inventory):
        if not self._manager.tool_names and not self._manager.warnings:
            return ()
        return (
            PromptContribution(
                key="extensions.mcp.catalog",
                content_kind="corki.mcp.catalog",
                template_name="extensions/mcp/catalog",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                order=25,
                variables={
                    "tools": inventory,
                    "warnings": "\n".join(f"- {value}" for value in self._manager.warnings)
                    or "- none",
                },
            ),
        )
