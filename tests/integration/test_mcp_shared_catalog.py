"""Cross-Runtime catalog discovery never borrows a previous live connection."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.model_context import ModelContextInfo
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient, MCPClient
from corki.mcp.manager import MCPManager
from corki.mcp.tool_catalog_cache import CatalogSnapshot, MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.session_source import SessionSource, SubAgentSource
from corki.protocol.tools import ToolCall, ToolConcurrency
from corki.tools import ToolRegistry


class CatalogClient(MCPClient):
    def __init__(self, settings, *, pending=False):
        super().__init__(settings)
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.closed = False
        self.calls = []
        self.read_only = True
        self.fail = False
        if not pending:
            self.release.set()

    async def start(self):
        self.server_instructions = "Shared catalog test instructions"

    async def list_tools(self):
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise ValueError("catalog unavailable")
        return [
            {
                "name": "lookup",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": self.read_only},
            }
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "current connection"}]}

    async def aclose(self):
        self.closed = True

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


def test_second_runtime_sees_cached_definition_while_own_connection_pending(tmp_path, monkeypatch):
    async def scenario():
        server = MCPServerSettings("shared", "http", url=f"https://catalog.test/{tmp_path.name}")
        clients = []
        requests = []

        def factory(settings):
            client = CatalogClient(settings, pending=bool(clients))
            clients.append(client)
            return client

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            execution_permissions=None,
            mcp_servers=(server,),
            tool_search_mode="disabled",
            mcp_optional_startup_grace_ms=1,
        )
        for index in range(2):
            runtime = await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / f"{index}.db",
                model=Model(),
                registry=ToolRegistry(),
            )
            try:
                if index == 0:
                    await runtime._mcp_manager.start()
                await asyncio.wait_for(_consume(runtime), 3)
                assert any(t.name == "mcp__shared::lookup" for t in requests[-1].tools)
                if index == 1:
                    assert not clients[-1].release.is_set()
                    assert clients[0].closed
            finally:
                await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())


async def _consume(runtime):
    return [event async for event in runtime.stream("Use the available tools")]


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("lazy", [False, True])
def test_cached_search_then_call_waits_live_connection_and_current_approval(
    tmp_path, monkeypatch, mode, lazy
):
    async def scenario():
        cache = MCPToolCatalogCache()
        settings = MCPServerSettings("shared", "http", url="https://shared.test")
        clients = []
        created = asyncio.Event()

        def factory(declaration):
            client = CatalogClient(declaration, pending=bool(clients))
            client.read_only = not clients
            clients.append(client)
            if len(clients) > 1:
                created.set()
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        seed = MCPManager((settings,), ToolRegistry(), tool_catalog_cache=cache)
        await seed.start()
        await seed.aclose()
        requests, approvals = [], []
        admitting = asyncio.Event()

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    assert "tool_search" in [t.name for t in request.tools]
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "shared lookup"})
                elif len(requests) == 2:
                    found = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert [t.name for t in found.discovered_tools] == ["mcp__shared::lookup"]
                    assert "mcp__shared::lookup" in {t.name for t in request.tools}
                    assert found.discovered_tools[0].concurrency == ToolConcurrency.EXCLUSIVE
                    assert clients[-1].calls == []
                    if lazy:
                        assert len(clients) == 1  # search did not instantiate a client
                    call = ToolCall(new_tool_call_id(), "mcp__shared::lookup", {})
                else:
                    result = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "mcp__shared::lookup"
                    )
                    assert not result.is_error and "current connection" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(settings,),
                tool_search_mode=mode,
                api_mode="responses",
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                mcp_optional_startup_grace_ms=0,
            ),
            database_path=tmp_path / "call.db",
            model=Model(),
            registry=ToolRegistry(),
            mcp_tool_catalog_cache=cache,
            session_source=SessionSource.subagent(SubAgentSource("review"))
            if lazy
            else SessionSource(),
        )
        original = runtime._mcp_manager._wait_preparation

        async def prepare(generation, mode, *args, **kwargs):
            if mode == "server":
                admitting.set()
            return await original(generation, mode, *args, **kwargs)

        async def approve(settings, name, arguments, annotations, *, allow_persistent):
            approvals.append(annotations)
            assert allow_persistent is True
            assert annotations.read_only is False

        monkeypatch.setattr(runtime._mcp_manager, "_wait_preparation", prepare)
        monkeypatch.setattr(runtime._mcp_manager._approvals, "check", approve)
        consumer = asyncio.create_task(_consume(runtime))
        try:
            await asyncio.wait_for(admitting.wait(), 3)
            await asyncio.wait_for(created.wait(), 1)
            await asyncio.wait_for(clients[-1].entered.wait(), 1)
            assert not consumer.done() and not clients[-1].calls and not approvals
            clients[-1].release.set()
            events = await asyncio.wait_for(consumer, 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3 and len(approvals) == 1
            assert clients[0].calls == [] and clients[1].calls == [("lookup", {})]
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("transition", ["replace", "expire", "disable"])
def test_capture_rechecks_cache_after_waiting_for_other_required_server(monkeypatch, transition):
    async def scenario():
        now = [0.0]
        cache = MCPToolCatalogCache(clock=lambda: now[0])
        settings = MCPServerSettings("cached", "http", url="https://cached.test")
        other = replace(settings, name="other")
        entry = cache.context(settings)
        entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot(({"name": "old"},), None))
        clients = {}

        def factory(declaration):
            clients[declaration.name] = CatalogClient(declaration, pending=True)
            return clients[declaration.name]

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager((settings, other), registry, tool_catalog_cache=cache)
        captured = asyncio.Event()
        original = entry.current_tools_or

        def read(fallback=None):
            result = original(fallback)
            # Stage tasks also inspect the cache for lazy startup. This fault
            # window begins only once the capture itself has taken its fallback.
            if asyncio.current_task() is capture:
                captured.set()
            return result

        monkeypatch.setattr(entry, "current_tools_or", read)
        capture = asyncio.create_task(manager.capture_tools(required_servers=("other",)))
        try:
            await asyncio.wait_for(captured.wait(), 1)
            assert not capture.done()
            if transition == "replace":
                entry.publish_if_newest(
                    entry.begin_fetch(), CatalogSnapshot(({"name": "new"},), None)
                )
            elif transition == "expire":
                now[0] = 1801
            else:
                entry.disable()
            clients["other"].release.set()
            await asyncio.wait_for(capture, 1)
            names = {s.name for s in registry.specs() if s.name.startswith("mcp__cached")}
            assert names == (
                set()
                if transition == "disable"
                else {"mcp__cached::new" if transition == "replace" else "mcp__cached::old"}
            )
            assert not clients["cached"].release.is_set()
            # Expiry fallback belongs to this capture only.
            if transition == "expire":
                await manager.capture_tools(optional_startup_grace_ms=1)
                assert not any(s.name.startswith("mcp__cached") for s in registry.specs())
        finally:
            capture.cancel()
            await asyncio.gather(capture, return_exceptions=True)
            await manager.aclose()

    asyncio.run(scenario())


def test_failed_pending_server_removes_cached_tools(monkeypatch):
    async def scenario():
        settings = MCPServerSettings("cached", "http", url="https://cached.test")
        cache = MCPToolCatalogCache()
        entry = cache.context(settings)
        entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot(({"name": "old"},), None))
        client = CatalogClient(settings, pending=True)
        client.fail = True
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry, tool_catalog_cache=cache)
        try:
            await manager.capture_tools(optional_startup_grace_ms=0)
            assert any(s.name == "mcp__cached::old" for s in registry.specs())
            client.release.set()
            await manager.prepare_server("cached")
            assert not any(s.name.startswith("mcp__cached") for s in registry.specs())
            assert client.closed
            assert "catalog unavailable" in " ".join(manager.warnings)
        finally:
            await manager.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("retry", [False, True])
def test_http_initialize_opt_out_revokes_cache_before_tools_list_completes(monkeypatch, retry):
    async def scenario():
        settings = MCPServerSettings("cached", "http", url="https://cached.test")
        cache = MCPToolCatalogCache()
        entry = cache.context(settings)
        entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot(({"name": "old"},), None))
        listing, release = asyncio.Event(), asyncio.Event()
        attempts = 0

        async def handle(request):
            nonlocal attempts
            message = json.loads(request.content)
            if "id" not in message:
                return httpx.Response(202)
            if message["method"] == "initialize":
                attempts += 1
                if retry and attempts == 1:
                    return httpx.Response(503)
                result = {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "cached", "version": "1"},
                    "capabilities": {
                        "experimental": {"codex/tool-catalog-cache": {"cacheable": False}}
                    },
                }
            elif message["method"] == "tools/list":
                listing.set()
                await release.wait()
                result = {"tools": [{"name": "live", "inputSchema": {"type": "object"}}]}
            else:
                raise AssertionError(message)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
            )

        client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager((settings,), ToolRegistry(), tool_catalog_cache=cache)
        try:
            await manager.start_session()
            await asyncio.wait_for(listing.wait(), 2)
            assert attempts == 1 + int(retry)
            assert not client.tool_catalog_cacheable
            assert entry.current_tools_or() is None
            await manager.capture_tools(optional_startup_grace_ms=1)
            assert manager.tool_names == (
                "list_mcp_resources",
                "list_mcp_resource_templates",
                "read_mcp_resource",
            )
            release.set()
            await manager.prepare_server("cached")
            assert "mcp__cached::live" in manager.tool_names
            assert entry.current_tools_or() is None
        finally:
            await manager.aclose()
        assert client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("configured_required", [False, True])
def test_cached_definition_does_not_skip_required_startup(monkeypatch, configured_required):
    async def scenario():
        settings = MCPServerSettings(
            "cached", "http", url="https://cached.test", required=configured_required
        )
        cache = MCPToolCatalogCache()
        entry = cache.context(settings)
        entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot(({"name": "old"},), None))
        client = CatalogClient(settings, pending=True)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager((settings,), ToolRegistry(), tool_catalog_cache=cache)
        capture = asyncio.create_task(
            manager.capture_tools(
                optional_startup_grace_ms=1,
                required_servers=() if configured_required else ("cached",),
            )
        )
        try:
            await asyncio.wait_for(client.entered.wait(), 1)
            await asyncio.sleep(0.01)
            assert not capture.done()
            client.release.set()
            await asyncio.wait_for(capture, 1)
            assert "mcp__cached::lookup" in manager.tool_names
            assert "mcp__cached::old" not in manager.tool_names
        finally:
            capture.cancel()
            await asyncio.gather(capture, return_exceptions=True)
            await manager.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "definition",
    [
        {"name": "bad", "annotations": {"readOnlyHint": "yes"}},
        {"name": "bad", "inputSchema": []},
        {"name": ""},
    ],
)
def test_invalid_catalog_does_not_poison_shared_definition_cache(monkeypatch, definition):
    async def scenario():
        settings = MCPServerSettings("invalid", "http", url="https://invalid.test")
        cache = MCPToolCatalogCache()
        client = CatalogClient(settings)

        async def list_tools():
            return [definition]

        monkeypatch.setattr(client, "list_tools", list_tools)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager((settings,), ToolRegistry(), tool_catalog_cache=cache)
        try:
            await manager.start()
            assert manager.warnings
            assert manager.tool_names == (
                "list_mcp_resources",
                "list_mcp_resource_templates",
                "read_mcp_resource",
            )
            assert cache.context(settings).current_tools_or() is None
        finally:
            await manager.aclose()

    asyncio.run(scenario())
