import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import StdioMCPClient
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


@pytest.mark.parametrize("change", ["policy", "reader_closed", "process_exited"])
def test_stdio_reconciliation_reuses_live_session_and_replaces_terminal_transport(
    tmp_path, monkeypatch, change
):
    async def scenario():
        clients, processes, listings = [], [], []

        class Client(StdioMCPClient):
            async def start(self):
                await super().start()
                processes.append(self._process)

            async def list_tools(self):
                listings.append(self._process.pid)
                return await super().list_tools()

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        script = Path(__file__).parents[1] / "fixtures" / "refresh_mcp_server.py"
        settings = MCPServerSettings(
            "docs",
            "stdio",
            command=sys.executable,
            args=(str(script), "same-session"),
            cwd=tmp_path,
        )
        manager = MCPManager((settings,), ToolRegistry())
        try:
            await manager.start()
            first = processes[0]
            if change == "reader_closed":
                clients[0]._reader_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await clients[0]._reader_task
                assert first.returncode is None
            elif change == "process_exited":
                first.terminate()
                await asyncio.wait_for(first.wait(), 3)
            manager.request_reconcile(
                (replace(settings, timeout_seconds=60, tool_output_token_limits=(("lookup", 30),)),)
            )
            await manager.refresh_if_dirty()
            assert len(clients) == (1 if change == "policy" else 2)
            current = manager._clients_by_name["docs"].client._process
            assert (current.pid == first.pid) == (change == "policy")
            assert listings == [p.pid for p in processes]
            release = tmp_path / "release"
            release.touch()
            result = await manager.call_tool(
                "docs",
                "lookup",
                {
                    "entered": str(tmp_path / "entered"),
                    "release": str(release),
                },
            )
            assert result == {"content": [{"type": "text", "text": "same-session"}]}
            manager.request_refresh()
            await manager.refresh_if_dirty()
            assert manager._clients_by_name["docs"].client._process.pid != current.pid
        finally:
            await manager.aclose()
        assert all(p.returncode is not None for p in processes)
        assert all(
            c._process is None and c._reader_task.done() and c._stderr_task.done() for c in clients
        )

    asyncio.run(scenario())
