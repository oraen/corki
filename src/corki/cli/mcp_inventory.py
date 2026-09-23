"""Owned, asynchronous MCP discovery for the local command UI."""

import asyncio
from collections import defaultdict


class MCPInventory:
    def __init__(self, runtime, ui):
        self.runtime = runtime
        self.ui = ui
        self.task = None

    def start(self):
        if self.task is not None and not self.task.done():
            self.ui.show_notice("MCP tool discovery is already running.")
            return
        if update := getattr(self.ui, "set_mcp_loading", None):
            update(True)
        else:
            self.ui.show_notice("Loading MCP tools…")
        self.task = asyncio.create_task(self._load(), name="corki-cli-mcp-inventory")

    async def _load(self):
        try:
            entries = await self.runtime.mcp_tool_catalog()
            servers = defaultdict(list)
            for entry in entries:
                servers[entry.server_name].append(entry.name)
            lines = ["MCP tools"]
            if not entries:
                lines.append("No MCP tools currently available.")
            for server, names in sorted(servers.items()):
                lines.append(f"\n{server} ({len(names)} tools)")
                lines.extend(f"  • {name}" for name in sorted(names))
            lines.append("\nDiscovery is not execution authorization or a connection health check.")
            lines.append("Use /mcp refresh to request reconnection.")
            # Server-provided names are text, never terminal control sequences.
            output = "\n".join(lines)
            self.ui.show_notice("".join(c for c in output if c.isprintable() or c == "\n"))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - discovery must not terminate the CLI
            self.ui.show_notice(
                f"MCP tool discovery failed ({type(error).__name__}). Retry with /mcp."
            )
        finally:
            if update := getattr(self.ui, "set_mcp_loading", None):
                update(False)

    async def aclose(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        # A task cancelled before its first execution never reaches _load's finally.
        if update := getattr(self.ui, "set_mcp_loading", None):
            update(False)
