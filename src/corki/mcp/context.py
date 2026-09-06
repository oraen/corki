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
        if not self._manager.tool_names and not self._manager.warnings:
            return ()
        return (
            PromptContribution(
                key="extensions.mcp.catalog",
                template_name="extensions/mcp/catalog",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                order=25,
                variables={
                    "tools": self._manager.prompt_inventory,
                    "warnings": "\n".join(f"- {value}" for value in self._manager.warnings)
                    or "- none",
                },
            ),
        )
