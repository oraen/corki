"""Ordinary startup ownership and approval waiting on exact-call authority."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import MCPServerSettings
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import MCPClient, MCPProtocolError
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


def definition(name):
    return {"name": name, "inputSchema": {"type": "object"}, "_meta": {"connector_id": "fixture"}}


class Client(MCPClient):
    def __init__(self, settings):
        super().__init__(settings)
        self.tools = (definition("old"),)
        self.lists, self.calls = 0, []
        self.on_list = self.on_call = None
        self.closed = False

    async def start(self):
        pass

    async def list_tools(self):
        self.lists += 1
        if self.on_list is not None:
            await self.on_list()
        return self.tools

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        if self.on_call is not None:
            await self.on_call()
        return {"content": [{"type": "text", "text": name}]}

    async def aclose(self):
        self.closed = True

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


def manager_fixture(monkeypatch, *, server, approval_policy="never"):
    clients = []

    def factory(settings):
        client = Client(settings)
        clients.append(client)
        return client

    monkeypatch.setattr("corki.mcp.manager.create_client", factory)
    settings = MCPServerSettings(server, "http", url="https://fixture.invalid")
    registry = ToolRegistry()
    manager = MCPManager(
        (settings,),
        registry,
        approval_policy=approval_policy,
        catalog=MCPCatalog(
            tuple(
                MCPRegistration(s, MCPCatalogSource("compatibility", "fixture"))
                for s in (settings,)
            )
        ),
    )
    return manager, clients, registry, settings


@pytest.mark.parametrize("server", ["codex_apps", "ordinary"])
@pytest.mark.parametrize("replace_pending", [False, True])
@pytest.mark.parametrize("finish", ["cancel", "shutdown"])
def test_cold_operation_cancellation_and_shutdown_own_selected_startup(
    monkeypatch, server, replace_pending, finish
):
    async def scenario():
        manager, clients, _, settings = manager_fixture(monkeypatch, server=server)
        entered, release, selected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_list = Client.list_tools

        async def list_tools(client):
            if client is clients[0]:
                entered.set()
                await release.wait()
            return await original_list(client)

        monkeypatch.setattr(Client, "list_tools", list_tools)
        task = None
        try:
            await manager._capture("publish")
            await asyncio.wait_for(entered.wait(), 1)
            original = manager._preparation
            wait = manager._wait_preparation

            async def observe(generation, mode, *args, **kwargs):
                if generation is original and mode == "server":
                    selected.set()
                return await wait(generation, mode, *args, **kwargs)

            monkeypatch.setattr(manager, "_wait_preparation", observe)
            task = asyncio.create_task(manager.call_tool(server, "old", {}))
            await asyncio.wait_for(selected.wait(), 1)
            assert original.binding_users == 1
            if replace_pending:
                manager.request_refresh((replace(settings, url="https://new.invalid"),))
                await manager.refresh_if_dirty()
                assert manager._preparation is not original
                assert not original.tasks[server].done()
            if finish == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
                if not replace_pending:
                    assert not original.tasks[server].done()
                    release.set()
                    assert await original.tasks[server]
                else:
                    assert original.tasks[server].cancelled()
                    assert clients[0].closed
            else:
                await asyncio.wait_for(manager.aclose(), 1)
                with pytest.raises(MCPProtocolError, match="startup was cancelled"):
                    await asyncio.wait_for(task, 1)
            assert original.binding_users == 0
            assert not any(client.calls for client in clients)
        finally:
            release.set()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("server", ["codex_apps", "ordinary"])
@pytest.mark.parametrize("finish", ["execute", "stale", "cancel"])
def test_reviewed_grant_is_not_applied_while_waiting_for_catalog_authority(
    monkeypatch, finish, server
):
    async def scenario():
        manager, clients, _, _ = manager_fixture(
            monkeypatch, server=server, approval_policy="on-request"
        )
        reviewed = asyncio.Event()
        effects = []
        check = manager._approvals.check

        async def review(*args, **kwargs):
            decision = await check(*args, **kwargs)
            reviewed.set()
            return decision

        async def host(request):
            manager.elicitations.respond(
                request.server_name, request.request_id, "accept", content={"remember": True}
            )

        async def memory():
            effects.append("memory")

        manager.elicitations.handler = host
        monkeypatch.setattr(manager._approvals, "check", review)
        task = None
        try:
            await manager.start()
            authority = manager._preparation.catalog_revision
            async with manager.prepare_call(server, "old") as prepared:
                async with authority.write():
                    task = asyncio.create_task(
                        prepared.call(
                            {},
                            on_external_context=memory,
                            on_output_token_limit=lambda _: effects.append("output"),
                        )
                    )
                    await asyncio.wait_for(reviewed.wait(), 1)
                    assert not task.done()
                    assert not manager._approvals._session and not clients[0].calls
                    assert effects == ["output"]
                    if finish == "cancel":
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 1)
                    elif finish == "stale":
                        authority.value += 1
                if finish == "stale":
                    with pytest.raises(MCPProtocolError, match="catalog changed"):
                        await asyncio.wait_for(task, 1)
                elif finish == "execute":
                    await asyncio.wait_for(task, 1)
                assert not prepared.connection._users
            executes = finish == "execute"
            assert clients[0].calls == (["old"] if executes else [])
            assert effects == (["output", "memory"] if executes else ["output"])
            assert manager._approvals._session == (
                {(server, None, None, "old")} if executes else set()
            )
            assert not manager.elicitations._pending
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())
