"""Cached subagent catalogs do not instantiate transports until needed."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import MCPClient, MCPProtocolError
from corki.mcp.names import normalize_tool_names
from corki.mcp.tool_catalog_cache import CatalogSnapshot, MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.session_source import SessionSource, SessionSourceKind, SubAgentSource
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("name", ["lazy", "codex_apps"])
def test_first_subagent_runtime_uses_cache_without_constructing_client(tmp_path, monkeypatch, name):
    async def scenario():
        settings = MCPServerSettings(name, "http", url="https://lazy.test", required=True)
        cache = MCPToolCatalogCache()
        entry = cache.context(settings)
        entry.publish_if_newest(
            entry.begin_fetch(),
            CatalogSnapshot(
                (
                    {
                        "name": "lookup",
                        "inputSchema": {"type": "object"},
                        "_meta": {"connector_id": "fixture"},
                    },
                ),
                None,
            ),
        )
        factories = []

        def factory(settings):
            factories.append(settings)
            raise AssertionError("a dormant server must not instantiate a transport")

        class Model:
            async def stream(self, request):
                assert any(t.name == f"mcp__{name}::lookup" for t in request.tools)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(settings,),
                tool_search_mode="disabled",
            ),
            database_path=tmp_path / "lazy.db",
            model=Model(),
            registry=ToolRegistry(),
            session_source=SessionSource.subagent(SubAgentSource("review")),
            mcp_tool_catalog_cache=cache,
        )
        try:
            events = [e async for e in runtime.stream("Inspect the tool catalog")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert factories == []
            runtime._mcp_manager.cancel_startup()
            assert factories == []
        finally:
            await runtime.aclose()
        assert factories == []

    asyncio.run(scenario())


class LazyClient(MCPClient):
    def __init__(self, settings, started):
        super().__init__(settings)
        self.started = started
        self.release = asyncio.Event()
        self.closed = False
        self.calls = []

    async def start(self):
        self.started.set()
        await self.release.wait()

    async def list_tools(self):
        return [
            {
                "name": "lookup",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
            }
        ]

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        return {"content": [{"type": "text", "text": "live result"}]}

    async def aclose(self):
        self.closed = True

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


async def setup_runtime(
    tmp_path,
    monkeypatch,
    model,
    *,
    source=None,
    server=None,
    definition=None,
    catalog=None,
    cache=None,
    tool_mode="direct",
    thread_id=None,
):
    server = server or MCPServerSettings("lazy", "http", url="https://lazy.test")
    cache = cache or MCPToolCatalogCache()
    entry = cache.context(server)
    if definition is None:
        definition = {"name": "lookup"}
    entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot((definition,), None))
    started, clients = asyncio.Event(), []

    def factory(settings):
        client = LazyClient(settings, started)
        clients.append(client)
        return client

    monkeypatch.setattr("corki.mcp.manager.create_client", factory)
    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            execution_permissions=None,
            mcp_servers=(server,),
            tool_search_mode="disabled",
            mcp_optional_startup_grace_ms=1,
            tool_mode=tool_mode,
        ),
        database_path=tmp_path / "lazy.db",
        thread_id=thread_id,
        model=model,
        registry=ToolRegistry(),
        session_source=source or SessionSource.subagent(SubAgentSource("review")),
        mcp_tool_catalog_cache=cache,
        mcp_catalog=catalog,
    )
    return runtime, clients, started, entry


class EmptyModel:
    async def stream(self, request):
        yield ModelCompleted(())

    async def aclose(self):
        pass


@pytest.mark.parametrize(
    "source,expected",
    [
        (SessionSource(SessionSourceKind.CLI), True),
        (SessionSource.internal("memory_consolidation"), True),
        (SessionSource.internal("guardian"), True),
        (SessionSource.from_startup_arg("subagent:review"), True),
        (SessionSource.subagent(SubAgentSource("review")), False),
    ],
)
def test_lazy_policy_uses_exact_host_source(tmp_path, monkeypatch, source, expected):
    async def scenario():
        runtime, clients, _, _ = await setup_runtime(
            tmp_path, monkeypatch, EmptyModel(), source=source
        )
        try:
            await runtime._ensure_ready()
            assert bool(clients) is expected
        finally:
            await runtime.aclose()
        assert all(c.closed for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("exclusion", ["filtered", "ui_hidden", "selected_plugin", "force_refresh"])
def test_cache_presence_alone_does_not_allow_lazy_startup(tmp_path, monkeypatch, exclusion):
    async def scenario():
        server = MCPServerSettings("lazy", "http", url="https://lazy.test")
        if exclusion == "filtered":
            server = replace(server, enabled_tools=())
        definition = {"name": "lookup"}
        if exclusion == "ui_hidden":
            definition["_meta"] = {"ui": {"visibility": ["app"]}}
        catalog = (
            MCPCatalog((MCPRegistration(server, MCPCatalogSource("selected_plugin", "chosen")),))
            if exclusion == "selected_plugin"
            else None
        )
        runtime, clients, started, _ = await setup_runtime(
            tmp_path,
            monkeypatch,
            EmptyModel(),
            server=server,
            definition=definition,
            catalog=catalog,
        )
        try:
            if exclusion == "force_refresh":
                runtime.request_mcp_refresh()
            await runtime._ensure_ready()
            await asyncio.wait_for(started.wait(), 1)
            assert len(clients) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("trigger", ["tool_call", "explicit_input"])
def test_dormant_server_starts_at_exact_use_and_routes_result(tmp_path, monkeypatch, trigger):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "mcp__lazy::lookup", {}), turn, step
                            ),
                        )
                    )
                else:
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    assert not result.is_error and "live result" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime, clients, started, _ = await setup_runtime(tmp_path, monkeypatch, Model())
        await runtime._ensure_ready()
        assert clients == []

        async def consume():
            prompt = "[$docs](mcp://lazy)" if trigger == "explicit_input" else "Use lookup"
            return [event async for event in runtime.stream(prompt)]

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert len(clients) == 1 and clients[0].calls == []
            assert len(requests) == (0 if trigger == "explicit_input" else 1)
            clients[0].release.set()
            events = await asyncio.wait_for(consumer, 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert clients[0].calls == ["lookup"] and len(requests) == 2
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize("invalidate", ["expire", "disable"])
def test_invalidated_cache_activates_dormant_server(tmp_path, monkeypatch, invalidate):
    async def scenario():
        now = [0.0]
        cache = MCPToolCatalogCache(clock=lambda: now[0])
        runtime, clients, started, entry = await setup_runtime(
            tmp_path, monkeypatch, EmptyModel(), cache=cache
        )
        try:
            await runtime._ensure_ready()
            assert not clients
            if invalidate == "expire":
                now[0] = 1801
            else:
                entry.disable()
            await runtime._refresh_tools()
            await asyncio.wait_for(started.wait(), 1)
            assert len(clients) == 1
            assert not any(t.name.startswith("mcp__lazy") for t in runtime._registry.specs())
            clients[0].release.set()
            await runtime._mcp_manager.prepare_server("lazy")
            assert "mcp__lazy::lookup" in runtime._mcp_manager.tool_names
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_idle_cancel_preserves_dormant_but_close_joins_it_without_factory(tmp_path, monkeypatch):
    async def scenario():
        runtime, clients, _, _ = await setup_runtime(tmp_path, monkeypatch, EmptyModel())
        await runtime._ensure_ready()
        generation = runtime._mcp_manager._preparation
        task = generation.tasks["lazy"]
        runtime._mcp_manager.cancel_startup()
        await asyncio.sleep(0)
        assert not task.done() and generation.is_dormant("lazy")
        await runtime.aclose()
        assert task.done() and not clients

    asyncio.run(scenario())


def test_app_only_tools_are_hidden_in_cached_and_live_model_bindings(tmp_path, monkeypatch):
    async def scenario():
        definition = {"name": "lookup", "_meta": {"ui": {"visibility": ["app"]}}}
        runtime, clients, _, _ = await setup_runtime(
            tmp_path, monkeypatch, EmptyModel(), definition=definition
        )
        try:
            await runtime._ensure_ready()
            assert len(clients) == 1  # hidden-only caches do not permit lazy startup
            await runtime._refresh_tools()
            assert "mcp__lazy::lookup" not in runtime._mcp_manager.tool_names

            async def list_tools():
                return [definition]

            monkeypatch.setattr(clients[0], "list_tools", list_tools)
            clients[0].release.set()
            await runtime._mcp_manager.prepare_server("lazy")
            assert "mcp__lazy::lookup" not in runtime._mcp_manager.tool_names
            with pytest.raises(KeyError, match="unknown MCP tool"):
                await runtime._mcp_manager.call_tool("lazy", "lookup", {})
            assert clients[0].calls == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_app_only_name_still_participates_in_canonical_collision_resolution(tmp_path, monkeypatch):
    async def scenario():
        runtime, clients, _, entry = await setup_runtime(tmp_path, monkeypatch, EmptyModel())
        definitions = ({"name": "a-b"}, {"name": "a_b", "_meta": {"ui": {"visibility": ["app"]}}})
        entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot(definitions, None))
        expected = next(
            t.canonical
            for t in normalize_tool_names((("lazy", "a-b"), ("lazy", "a_b")))
            if t.remote == "a-b"
        )
        try:
            await runtime._ensure_ready()
            assert not clients
            assert runtime._mcp_manager.tool_names == (
                expected,
                "list_mcp_resources",
                "list_mcp_resource_templates",
                "read_mcp_resource",
            )
            assert expected != "mcp__lazy::a_b"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_replacing_unused_dormant_server_does_not_instantiate_it(tmp_path, monkeypatch):
    async def scenario():
        runtime, clients, _, _ = await setup_runtime(tmp_path, monkeypatch, EmptyModel())
        try:
            await runtime._ensure_ready()
            old = runtime._mcp_manager._preparation.tasks["lazy"]
            runtime.request_mcp_reconcile(())
            await runtime._refresh_tools()
            assert old.done() and clients == []
            assert "mcp__lazy::lookup" not in runtime._mcp_manager.tool_names
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cancelling_triggered_startup_closes_its_owned_client(tmp_path, monkeypatch):
    async def scenario():
        runtime, clients, started, _ = await setup_runtime(tmp_path, monkeypatch, EmptyModel())
        await runtime._ensure_ready()
        call = asyncio.create_task(runtime._mcp_manager.call_tool("lazy", "lookup", {}))
        try:
            await asyncio.wait_for(started.wait(), 1)
            runtime._mcp_manager.cancel_startup()
            with pytest.raises(MCPProtocolError, match="startup was cancelled"):
                await asyncio.wait_for(call, 1)
            assert clients[0].closed and clients[0].calls == []
        finally:
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
