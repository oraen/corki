"""Pending physical startup survives a new view with unchanged transport identity."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.mcp.manager import MCPManager
from corki.mcp.runtime_environment import MCPHTTPEnvironment, MCPRuntimeContext
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


class PendingClient(MCPClient):
    def __init__(self, settings, phase):
        super().__init__(settings)
        self.phase = phase
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.cancelled = asyncio.Event()
        self.starts, self.lists, self.closes = 0, 0, 0
        self.calls = []

    async def pause(self, phase):
        if self.phase == phase:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    async def start(self):
        self.starts += 1
        await self.pause("initialize")

    async def list_tools(self):
        self.lists += 1
        await self.pause("list")
        return [{"name": name, "inputSchema": {"type": "object"}} for name in ("old", "new")]

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        return {"content": [{"type": "text", "text": name}]}

    async def aclose(self):
        self.closes += 1

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


@pytest.mark.parametrize("phase", ["initialize", "list"])
def test_runtime_policy_update_retains_pending_transport(tmp_path, monkeypatch, phase):
    async def scenario():
        declaration = MCPServerSettings(
            "pending", "http", url="https://pending.test", enabled_tools=("old",)
        )
        clients = []

        def factory(settings):
            client = PendingClient(settings, phase)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        requests, approvals = [], []

        class CallingModel:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    assert any(t.name == "mcp__pending::new" for t in request.tools)
                    assert not any(t.name == "mcp__pending::old" for t in request.tools)
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "mcp__pending::new", {}), turn, step
                            ),
                        )
                    )
                else:
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    assert not result.is_error and "new" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(declaration,),
                tool_search_mode="disabled",
                mcp_optional_startup_grace_ms=1,
            ),
            model=CallingModel(),
            registry=ToolRegistry(),
            database_path=tmp_path / "reuse.db",
        )
        try:
            await runtime._ensure_ready()
            await asyncio.wait_for(clients[0].entered.wait(), 1)
            for index in range(3):
                runtime.request_mcp_reconcile(
                    (
                        replace(
                            declaration,
                            enabled_tools=("new",),
                            tool_output_token_limits=(("new", 100 + index),),
                            default_tools_approval_mode="prompt",
                        ),
                    )
                )
                await runtime._refresh_tools()
                assert len(clients) == 1 and clients[0].closes == 0
            clients[0].release.set()
            await runtime._mcp_manager.prepare_server("pending")
            assert clients[0].starts == clients[0].lists == 1
            assert "mcp__pending::new" in runtime._mcp_manager.tool_names
            assert "mcp__pending::old" not in runtime._mcp_manager.tool_names
            connection = runtime._mcp_manager._clients_by_name["pending"]
            assert connection.settings.tool_output_token_limits == (("new", 102),)
            assert connection.settings.default_tools_approval_mode == "prompt"

            async def approve(settings, name, arguments, annotations, *, allow_persistent):
                approvals.append(name)
                assert allow_persistent is True
                assert settings.tool_output_token_limits == (("new", 102),)
                assert settings.default_tools_approval_mode == "prompt"

            monkeypatch.setattr(runtime._mcp_manager._approvals, "check", approve)
            events = [event async for event in runtime.stream("Call the newly allowed tool")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert clients[0].calls == approvals == ["new"] and len(requests) == 2
        finally:
            await runtime.aclose()
        assert all(client.closes == 1 for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["url", "timeout", "credentials", "force", "remove", "disable"])
def test_incompatible_or_removed_pending_startup_is_not_reused(monkeypatch, change):
    async def scenario():
        declaration = MCPServerSettings(
            "pending",
            "http",
            url="https://pending.test",
            bearer_token_env_var="PENDING_REUSE_TOKEN",
        )
        monkeypatch.setenv("PENDING_REUSE_TOKEN", "old")
        clients = []

        def factory(settings):
            client = PendingClient(settings, "initialize")
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((declaration,), ToolRegistry())
        try:
            await manager.capture_tools(optional_startup_grace_ms=1)
            await asyncio.wait_for(clients[0].entered.wait(), 1)
            if change == "force":
                manager.request_refresh()
            else:
                modified = (
                    replace(declaration, url="https://different.test")
                    if change == "url"
                    else replace(declaration, timeout_seconds=1)
                    if change == "timeout"
                    else replace(declaration, enabled=False)
                    if change == "disable"
                    else declaration
                )
                if change == "credentials":
                    monkeypatch.setenv("PENDING_REUSE_TOKEN", "new")
                manager.request_reconcile(() if change == "remove" else (modified,))
            await manager.capture_tools(optional_startup_grace_ms=1)
            assert clients[0].closes == 1
            assert len(clients) == (1 if change in {"remove", "disable"} else 2)
            if len(clients) == 2:
                clients[1].release.set()
                await manager.prepare_server("pending")
                assert clients[1].starts == clients[1].lists == 1
        finally:
            await manager.aclose()
        assert all(client.closes == 1 for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("replacement", [False, True])
def test_real_http_pending_identity_includes_exact_environment_handle(replacement):
    async def scenario():
        class Carrier(httpx.AsyncBaseTransport):
            def __init__(self):
                self.entered, self.release = asyncio.Event(), asyncio.Event()
                self.starts, self.cancelled, self.closes = 0, 0, 0

            async def handle_async_request(self, request):
                packet = json.loads(request.content)
                if "id" not in packet:
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    self.starts += 1
                    self.entered.set()
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        self.cancelled += 1
                        raise
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "pending", "version": "1"},
                    }
                else:
                    assert packet["method"] == "tools/list"
                    result = {"tools": [{"name": "new", "inputSchema": {"type": "object"}}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            async def aclose(self):
                self.closes += 1

        carrier = Carrier()
        environment = MCPHTTPEnvironment("remote", carrier)
        declaration = MCPServerSettings(
            "pending", "http", url="https://pending.test", environment_id="remote"
        )
        manager = MCPManager(
            (declaration,), ToolRegistry(), runtime_context=MCPRuntimeContext((environment,))
        )
        try:
            await manager.capture_tools(optional_startup_grace_ms=1)
            await asyncio.wait_for(carrier.entered.wait(), 1)
            if replacement:
                manager.request_runtime_context(
                    MCPRuntimeContext((MCPHTTPEnvironment("remote", carrier),))
                )
            else:
                manager.request_reconcile((replace(declaration, enabled_tools=("new",)),))
            await manager.capture_tools(optional_startup_grace_ms=1)
            carrier.release.set()
            await manager.prepare_server("pending")
            assert carrier.starts == (2 if replacement else 1)
            assert carrier.cancelled == int(replacement)
            assert "mcp__pending::new" in manager.tool_names
        finally:
            await manager.aclose()
        assert carrier.closes == 0
        await carrier.aclose()

    asyncio.run(scenario())


def test_cancelled_handoff_releases_unpublished_successor_and_claims(monkeypatch):
    async def scenario():
        from corki.mcp.startup_handoff import PendingStartupClaims

        declaration = MCPServerSettings("pending", "http", url="https://pending.test")
        client = PendingClient(declaration, "initialize")
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager((declaration,), ToolRegistry())
        await manager.capture_tools(optional_startup_grace_ms=1)
        owner = manager._preparation.pending_connections["pending"]._owner
        entered, release = asyncio.Event(), asyncio.Event()
        original = PendingStartupClaims.release

        async def gated_release(claims):
            if claims.views:
                entered.set()
                try:
                    await release.wait()
                finally:
                    await original(claims)
            else:
                await original(claims)

        monkeypatch.setattr(PendingStartupClaims, "release", gated_release)
        manager.request_reconcile((replace(declaration, enabled_tools=("new",)),))
        refresh = asyncio.create_task(manager.start())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            refresh.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(refresh, 1)
            assert manager._preparation is None and manager.refresh_pending
            assert owner._references == 0 and owner._startup_task.done()
            assert client.closes == 1
        finally:
            refresh.cancel()
            await asyncio.gather(refresh, return_exceptions=True)
            await manager.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["before_successor", "after_successor"])
def test_explicit_startup_cancel_reaches_physical_owner_during_handoff(monkeypatch, phase):
    async def scenario():
        from corki.mcp.startup_handoff import PendingStartupClaims

        declaration = MCPServerSettings("pending", "http", url="https://pending.test")
        client = PendingClient(declaration, "initialize")
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager((declaration,), ToolRegistry())
        await manager.capture_tools(optional_startup_grace_ms=1)
        await client.entered.wait()
        entered, release = asyncio.Event(), asyncio.Event()
        if phase == "before_successor":
            original = manager._dispose_preparation

            async def dispose():
                await original()
                entered.set()
                await release.wait()

            monkeypatch.setattr(manager, "_dispose_preparation", dispose)
        else:
            original = PendingStartupClaims.release

            async def release_claims(claims):
                try:
                    if claims.views:
                        entered.set()
                        await release.wait()
                finally:
                    await original(claims)

            monkeypatch.setattr(PendingStartupClaims, "release", release_claims)
        manager.request_reconcile((replace(declaration, enabled_tools=("new",)),))
        refresh = asyncio.create_task(manager.start())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            manager.cancel_startup()
            await asyncio.wait_for(client.cancelled.wait(), 0.1)
        finally:
            refresh.cancel()
            release.set()
            await asyncio.gather(refresh, return_exceptions=True)
            await manager.aclose()
        assert client.closes == 1

    asyncio.run(scenario())


def test_close_during_handoff_never_constructs_a_late_successor(monkeypatch):
    async def scenario():
        declaration = MCPServerSettings("pending", "http", url="https://pending.test")
        clients = []

        def factory(settings):
            client = PendingClient(settings, "initialize")
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((declaration,), ToolRegistry())
        await manager.capture_tools(optional_startup_grace_ms=1)
        entered, release = asyncio.Event(), asyncio.Event()
        original = manager._dispose_preparation

        async def dispose():
            await original()
            entered.set()
            await release.wait()

        monkeypatch.setattr(manager, "_dispose_preparation", dispose)
        manager.request_reconcile((replace(declaration, url="https://changed.test"),))
        refresh = asyncio.create_task(manager.capture_tools(optional_startup_grace_ms=1))
        await asyncio.wait_for(entered.wait(), 1)
        closing = asyncio.create_task(manager.aclose())
        try:
            await asyncio.wait_for(clients[0].cancelled.wait(), 1)
            release.set()
            with pytest.raises(RuntimeError, match="closed"):
                await asyncio.wait_for(refresh, 1)
            await asyncio.wait_for(closing, 1)
            assert len(clients) == 1 and clients[0].closes == 1
            assert manager._startup_claims is None and manager._preparation is None
        finally:
            release.set()
            refresh.cancel()
            await asyncio.gather(refresh, closing, return_exceptions=True)
            await manager.aclose()

    asyncio.run(scenario())


def test_completed_physical_startup_is_not_cancelled_while_view_is_publishing(monkeypatch):
    async def scenario():
        from corki.mcp.connection import MCPConnection

        declaration = MCPServerSettings("pending", "http", url="https://pending.test")
        client = PendingClient(declaration, "initialize")
        client.release.set()
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        ready, release = asyncio.Event(), asyncio.Event()
        original = MCPConnection.start

        async def start(view):
            await original(view)
            ready.set()
            await release.wait()

        monkeypatch.setattr(MCPConnection, "start", start)
        manager = MCPManager((declaration,), ToolRegistry())
        starting = asyncio.create_task(manager.start())
        try:
            await asyncio.wait_for(ready.wait(), 1)
            manager.cancel_startup()
            await asyncio.sleep(0)
            assert not starting.done() and not client.closes
            release.set()
            await asyncio.wait_for(starting, 1)
            assert "mcp__pending::new" in manager.tool_names
        finally:
            release.set()
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
            await manager.aclose()

    asyncio.run(scenario())
