"""Ordinary policy replacement cannot rewrite an already selected MCP call."""

import asyncio
from dataclasses import replace

import pytest
from test_approval_reload import Client

from corki.config import MCPServerSettings
from corki.mcp.client import MCPProtocolError
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


@pytest.mark.parametrize("name", ["docs", "codex_apps"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_startup_requires_explicit_refresh(monkeypatch, name, cancelled):
    async def scenario():
        clients = []

        class FailedClient(Client):
            async def list_tools(self):
                if self is clients[0]:
                    if cancelled:
                        raise asyncio.CancelledError
                    raise MCPProtocolError("fixture startup failure")
                return await super().list_tools()

        def factory(settings):
            client = FailedClient(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager(
            (MCPServerSettings(name, "http", url="https://fixture.invalid"),), ToolRegistry()
        )
        try:
            await manager.start()
            for _ in range(3):
                await manager.capture_tools()
                with pytest.raises(
                    MCPProtocolError, match="startup.*cancelled|fixture startup failure"
                ):
                    await manager.call_tool(name, "old", {})
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(manager.list_tool_catalog() for _ in range(10)),
                    *(manager.capture_tools() for _ in range(10)),
                ),
                2,
            )
            assert all(result == () for result in results[:10])
            manager.cancel_startup()
            assert len(clients) == 1 and clients[0].closed
            manager.request_refresh()
            await manager.call_tool(name, "old", {})
            assert len(clients) == 2 and clients[1].calls == ["old"]
        finally:
            await manager.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("name", ["docs", "codex_apps"])
def test_selected_call_survives_filter_replacement(monkeypatch, pending, name):
    async def scenario():
        entered, release, prepared_ready, execute = (asyncio.Event() for _ in range(4))
        clients, limits, pollution = [], [], []

        class PendingClient(Client):
            async def list_tools(self):
                if pending:
                    entered.set()
                    await release.wait()
                return await super().list_tools()

        def factory(settings):
            client = PendingClient(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        settings = MCPServerSettings(name, "http", url="https://fixture.invalid")
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)

        async def mark():
            pollution.append(True)

        async def old_call():
            async with manager.prepare_call(name, "old") as prepared:
                prepared_ready.set()
                await execute.wait()
                await prepared.call(
                    {}, on_output_token_limit=limits.append, on_external_context=mark
                )

        task = asyncio.create_task(old_call())
        try:
            await asyncio.wait_for((entered if pending else prepared_ready).wait(), 2)
            manager.request_reconcile((replace(settings, disabled_tools=("old",)),))
            release.set()
            await asyncio.wait_for(prepared_ready.wait(), 2)
            await manager.start()
            assert registry.get(f"mcp__{name}::old") is None
            execute.set()
            await asyncio.wait_for(task, 2)
            assert clients[0].calls == ["old"]
            assert len(limits) == 1 and pollution == [True]
            manager.request_reconcile((settings,))
            await manager.start()
            assert registry.get(f"mcp__{name}::old") is not None
            await manager.call_tool(name, "old", {})
            assert sum(len(client.calls) for client in clients) == 2
        finally:
            release.set()
            execute.set()
            await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())
