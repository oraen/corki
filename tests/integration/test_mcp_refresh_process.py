import asyncio
import sys
from pathlib import Path

from corki.config import MCPServerSettings
from corki.mcp.client import StdioMCPClient
from corki.mcp.manager import MCPManager
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


def test_refresh_does_not_kill_old_stdio_process_with_an_in_flight_call(tmp_path, monkeypatch):
    async def scenario():
        script = Path(__file__).parents[1] / "fixtures" / "refresh_mcp_server.py"
        clients = []

        def factory(settings):
            client = StdioMCPClient(settings)
            clients.append(client)
            return client

        def settings(version):
            return MCPServerSettings(
                "docs", "stdio", command=sys.executable, args=(str(script), version), cwd=tmp_path
            )

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager((settings("amber"),), registry)
        await manager.start()
        registry.seal()
        first_process = clients[0]._process
        entered, release = tmp_path / "entered", tmp_path / "release"
        executor = ToolExecutor(registry, output_char_budget=1000)
        task = asyncio.create_task(
            executor.execute(
                ToolCall(
                    ToolCallId("in-flight"),
                    "mcp__docs::lookup",
                    {"entered": str(entered), "release": str(release)},
                ),
                ToolContext(cwd=tmp_path),
            )
        )

        async def wait_entered():
            while not entered.exists():
                assert first_process.returncode is None
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(wait_entered(), 3)
            manager.request_refresh((settings("cobalt"),))
            await manager.refresh_if_dirty()
            second_process = clients[1]._process
            assert first_process.pid != second_process.pid
            assert first_process.returncode is None and second_process.returncode is None
            release.touch()
            result = await asyncio.wait_for(task, 3)
            assert result.content.split("\nOutput:\n", 1)[1] == "amber" and not result.is_error
            # The existing stdio shutdown uses terminate; the contract here is
            # exit AFTER the completed call, not a particular process exit code.
            assert await asyncio.wait_for(first_process.wait(), 3) is not None
            assert second_process.returncode is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()
        assert all(client._process is None for client in clients)
        assert first_process.returncode is not None and second_process.returncode is not None
        assert all(client._reader_task.done() and client._stderr_task.done() for client in clients)

    asyncio.run(scenario())
