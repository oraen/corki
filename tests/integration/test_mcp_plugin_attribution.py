"""Plugin capability hints follow the winning host source, not losing declarations."""

import asyncio

import pytest
from test_mcp_input_requirements import setup_input_runtime
from test_mcp_pending_startup import PendingClient

from corki.config import MCPServerSettings
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.models import ModelCompleted
from corki.protocol import InputMention
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem


@pytest.mark.parametrize("winner", ["sample", "config", "other"])
@pytest.mark.parametrize("mode", ["direct", "compatible", "native"])
def test_selected_plugin_hint_uses_winning_server_source(tmp_path, monkeypatch, winner, mode):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                hints = [
                    i
                    for i in request.items
                    if isinstance(i, ContextItem)
                    and i.turn_id == turn
                    and i.content_kind == "plugins.instructions"
                ]
                assert bool(hints) is (winner == "sample"), (
                    "a losing plugin declaration must not claim the winner's tools",
                    winner,
                    hints,
                )
                if hints:
                    assert "`pending`" in hints[0].content
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, clients, _ = setup_input_runtime(
            tmp_path, monkeypatch, Model(), kind="plugin", mode=mode
        )

        def ready_client(settings):
            client = PendingClient(settings)
            client.release.set()
            clients[settings.name] = client
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", ready_client)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            declaration = MCPRegistration(
                MCPServerSettings("pending", "http", url="https://pending.test"),
                MCPCatalogSource("plugin", "sample"),
            )
            actions = (declaration,)
            if winner != "sample":
                actions += (
                    MCPRegistration(
                        declaration.settings,
                        MCPCatalogSource()
                        if winner == "config"
                        else MCPCatalogSource("selected_plugin", "other"),
                    ),
                )
            runtime.request_mcp_catalog(MCPCatalog(actions))
            events = [
                e
                async for e in runtime.stream(
                    "", mentions=(InputMention("display", "plugin://sample"),)
                )
            ]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1
            assert runtime._registry.snapshot().mcp_server_name("mcp__pending::lookup") == "pending"
        finally:
            await runtime.aclose()
        assert clients and all(client.closed for client in clients.values())

    asyncio.run(scenario())


def test_provenance_refresh_reuses_transport_but_not_old_plugin_hint(tmp_path, monkeypatch):
    async def scenario():
        requests, opened = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                hints = [
                    i
                    for i in request.items
                    if isinstance(i, ContextItem)
                    and i.turn_id == request.items[-1].turn_id
                    and i.content_kind == "plugins.instructions"
                ]
                assert bool(hints) is (len(requests) == 1)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, _, _ = setup_input_runtime(tmp_path, monkeypatch, Model(), kind="plugin")

        def factory(settings):
            client = PendingClient(settings)
            client.release.set()
            opened.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        try:
            first = [e async for e in runtime.stream("[@sample](plugin://sample)")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            frozen = runtime._registry.snapshot()
            assert frozen.mcp_plugin_id("mcp__pending::lookup") == "sample"
            (registration,) = runtime.mcp_catalog.servers
            # Same effective settings, new host owner: reuse the transport while
            # rebuilding provenance from the new winning source.
            runtime.request_mcp_catalog(MCPCatalog((MCPRegistration(registration.settings),)))
            second = [e async for e in runtime.stream("[@sample](plugin://sample)")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert len(opened) == 1 and len(requests) == 2
            assert runtime._registry.snapshot().mcp_plugin_id("mcp__pending::lookup") is None
            assert frozen.mcp_plugin_id("mcp__pending::lookup") == "sample"
        finally:
            await runtime.aclose()
        assert opened and all(client.closed for client in opened)

    asyncio.run(scenario())
