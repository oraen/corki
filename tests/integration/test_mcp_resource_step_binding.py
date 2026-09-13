"""Resource calls retain sampling-time readiness and the exact MCP generation."""

import asyncio
import json
import re

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools import ToolRegistry


def resource_call(mode, name, args, turn):
    call = (
        ToolCall(new_tool_call_id(), name, args)
        if mode == "direct"
        else ToolCall(
            new_tool_call_id(),
            "exec",
            None,
            input_kind="freeform",
            raw_arguments=f"text(await tools.{name}({json.dumps(args)}));",
        )
    )
    return ModelCompleted((ToolCallItem(call, turn, new_step_id()),))


class ResourceServers:
    def __init__(self):
        self.clients = []
        self.operations = []
        self.pending = asyncio.Event()
        self.started = asyncio.Event()
        self.delay_first_late = False
        self.counts = {}
        self.operation_gate = None
        self.operation_started = asyncio.Event()
        self.operation_finished = asyncio.Event()

    def factory(self, server):
        version = self.counts.get(server.name, 0)
        self.counts[server.name] = version + 1
        label = f"{server.name}-{version}"

        async def respond(request):
            packet = json.loads(request.content)
            method = packet["method"]
            if method == "notifications/initialized":
                return httpx.Response(202)
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": label, "version": "1"},
                }
            elif method == "tools/list":
                if self.delay_first_late and server.name == "late" and version == 0:
                    self.started.set()
                    await self.pending.wait()
                result = {"tools": []}
            else:
                self.operations.append((label, method))
                self.operation_started.set()
                try:
                    if self.operation_gate is not None:
                        await self.operation_gate.wait()
                finally:
                    self.operation_finished.set()
                if method == "resources/list":
                    result = {"resources": [{"uri": "fixture:value", "name": label}]}
                elif method == "resources/templates/list":
                    result = {"resourceTemplates": [{"uriTemplate": "fixture:{id}", "name": label}]}
                else:
                    assert method == "resources/read"
                    result = {"contents": [{"uri": "fixture:value", "text": label}]}
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
            )

        client = HttpMCPClient(server, transport=httpx.MockTransport(respond))
        self.clients.append(client)
        return client


@pytest.mark.parametrize("mode", ["direct", "code_mode"])
@pytest.mark.parametrize(
    "name,args,method",
    [
        ("list_mcp_resources", {}, "resources/list"),
        ("list_mcp_resources", {"server": "ready"}, "resources/list"),
        ("list_mcp_resource_templates", {}, "resources/templates/list"),
        (
            "list_mcp_resource_templates",
            {"server": "ready"},
            "resources/templates/list",
        ),
        ("read_mcp_resource", {"server": "ready", "uri": "fixture:value"}, "resources/read"),
    ],
)
def test_refresh_after_sampling_keeps_old_resource_binding(
    tmp_path, monkeypatch, mode, name, args, method
):
    async def scenario():
        servers = ResourceServers()
        monkeypatch.setattr("corki.mcp.manager.create_client", servers.factory)
        outputs = []

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn = request.items[-1].turn_id
                if self.steps == 1:
                    # A real model request already owns its Step before a host refresh.
                    runtime._mcp_manager.request_refresh()
                    await runtime._mcp_manager.refresh_if_dirty()
                else:
                    outputs.append([i for i in request.items if isinstance(i, ToolResultItem)][-1])
                if self.steps <= 2:
                    yield resource_call(mode, name, args, turn)
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode="disabled",
                tool_mode=mode,
                mcp_servers=(MCPServerSettings("ready", "http", url="https://ready.invalid"),),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "binding.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )
        try:
            events = [event async for event in runtime.stream("read both generations")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert servers.operations == [("ready-0", method), ("ready-1", method)]
            assert len(outputs) == 2 and all(not output.is_error for output in outputs)
            assert "ready-0" in outputs[0].content and "ready-1" not in outputs[0].content
            assert "ready-1" in outputs[1].content and "ready-0" not in outputs[1].content
            # Previous Steps do not keep unused transports until Runtime shutdown.
            assert servers.clients[0].is_closed
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in servers.clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode"])
@pytest.mark.parametrize("named", [False, True])
def test_resource_binding_retains_pending_generation_but_freezes_aggregate_readiness(
    tmp_path, monkeypatch, mode, named
):
    async def scenario():
        servers = ResourceServers()
        servers.delay_first_late = True
        monkeypatch.setattr("corki.mcp.manager.create_client", servers.factory)

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn = request.items[-1].turn_id
                if self.steps == 1:
                    assert servers.started.is_set() and not servers.pending.is_set()
                    if named:
                        # Only the old generation's named fallback may service this call.
                        runtime._mcp_manager.request_refresh()
                        await runtime._mcp_manager.refresh_if_dirty()
                    servers.pending.set()
                    yield resource_call(
                        mode, "list_mcp_resources", {"server": "late"} if named else {}, turn
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode="disabled",
                tool_mode=mode,
                mcp_optional_startup_grace_ms=10,
                mcp_servers=tuple(
                    MCPServerSettings(name, "http", url=f"https://{name}.invalid")
                    for name in ("ready", "late")
                ),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "pending.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )
        try:
            events = [event async for event in runtime.stream("read captured catalog")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert servers.operations == [("late-0" if named else "ready-0", "resources/list")]
        finally:
            servers.pending.set()
            await runtime.aclose()
        assert all(client.is_closed for client in servers.clients)

    asyncio.run(scenario())


def test_code_mode_resource_admitted_before_gate_retains_old_step(tmp_path, monkeypatch):
    async def scenario():
        servers = ResourceServers()
        monkeypatch.setattr("corki.mcp.manager.create_client", servers.factory)
        release = asyncio.Event()
        registry = ToolRegistry()

        class Hold:
            spec = ToolSpec("hold", "Wait at the exclusive gate", {})

            async def execute(self, call, context):
                await release.wait()
                return ToolResult(call.id, call.name, "released")

        registry.register(Hold())

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn = request.items[-1].turn_id
                if self.steps == 1:
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments="const hold = tools.hold({}); "
                        "const resource = tools.list_mcp_resources({server: 'ready'}); "
                        "yield_control(); await hold; text(await resource);",
                    )
                elif self.steps == 2:
                    assert len(runtime._code_mode.calls) == 2
                    runtime._mcp_manager.request_refresh()
                    await runtime._mcp_manager.refresh_if_dirty()
                    release.set()
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    cell = re.search(r"cell ID (\S+)", output.content)[1]
                    call = ToolCall(new_tool_call_id(), "wait", {"cell_id": cell})
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode="disabled",
                tool_mode="code_mode",
                mcp_servers=(MCPServerSettings("ready", "http", url="https://ready.invalid"),),
            ),
            model=Model(),
            registry=registry,
            database_path=tmp_path / "gate.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )
        try:
            events = [event async for event in runtime.stream("queue an old resource call")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert servers.operations == [("ready-0", "resources/list")]
            assert servers.clients[0].is_closed
        finally:
            release.set()
            await runtime.aclose()
        assert all(client.is_closed for client in servers.clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode"])
@pytest.mark.parametrize("action", ["model_failure", "cancel_tool", "close_tool"])
def test_resource_generation_is_released_on_turn_failure_or_cancel(
    tmp_path, monkeypatch, mode, action
):
    async def scenario():
        servers = ResourceServers()
        servers.operation_gate = asyncio.Event()
        monkeypatch.setattr("corki.mcp.manager.create_client", servers.factory)

        class Model:
            async def stream(self, request):
                runtime._mcp_manager.request_refresh()
                await runtime._mcp_manager.refresh_if_dirty()
                if action == "model_failure":
                    raise ValueError("fixture model failure after MCP refresh")
                yield resource_call(
                    mode, "list_mcp_resources", {"server": "ready"}, request.items[-1].turn_id
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode="disabled",
                tool_mode=mode,
                mcp_servers=(MCPServerSettings("ready", "http", url="https://ready.invalid"),),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "cleanup.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )

        events = []

        async def consume():
            async for event in runtime.stream("interrupt a captured resource"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            if action != "model_failure":
                await asyncio.wait_for(servers.operation_started.wait(), 3)
                assert servers.operations == [("ready-0", "resources/list")]
                assert not servers.clients[0].is_closed
                if action == "cancel_tool":
                    await runtime.cancel_active()
                else:
                    await runtime.aclose()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
                assert servers.operation_finished.is_set()
            else:
                await consumer
            expected = TurnFailed if action == "model_failure" else TurnCancelled
            assert isinstance(events[-1], expected), events[-1]
            assert servers.clients[0].is_closed
        finally:
            servers.operation_gate.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()
        assert all(client.is_closed for client in servers.clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("reopen", [False, True])
def test_background_resource_retains_old_generation_across_turn_but_not_restart(
    tmp_path, monkeypatch, reopen
):
    async def scenario():
        servers = ResourceServers()
        servers.operation_gate = asyncio.Event()
        monkeypatch.setattr("corki.mcp.manager.create_client", servers.factory)
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_search_mode="disabled",
            tool_mode="code_mode",
            mcp_servers=(MCPServerSettings("ready", "http", url="https://ready.invalid"),),
        )
        steps, cell_id = 0, None

        class Started:
            spec = ToolSpec(
                "resource_started",
                "Wait for resource admission",
                {},
                concurrency=ToolConcurrency.PARALLEL,
            )

            async def execute(self, call, context):
                await servers.operation_started.wait()
                return ToolResult(call.id, call.name, "started")

        class Model:
            async def stream(self, request):
                nonlocal steps, cell_id
                steps += 1
                turn = request.items[-1].turn_id
                if steps == 1:
                    runtime._mcp_manager.request_refresh()
                    await runtime._mcp_manager.refresh_if_dirty()
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=(
                            "const resource = tools.list_mcp_resources({server:'ready'}); "
                            "await tools.resource_started({}); "
                            "yield_control(); text(await resource);"
                        ),
                    )
                elif steps == 2:
                    await asyncio.wait_for(servers.operation_started.wait(), 3)
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    cell_id = re.search(r"cell ID (\S+)", output.content)[1]
                    yield ModelCompleted(
                        (AssistantMessageItem("background running", turn, new_step_id()),)
                    )
                    return
                elif steps == 3:
                    servers.operation_gate.set()
                    call = ToolCall(new_tool_call_id(), "wait", {"cell_id": cell_id})
                else:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert ("No live cell" if reopen else "ready-0") in output.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Started())
            return LangGraphRuntime.create(
                settings=settings,
                model=Model(),
                registry=registry,
                database_path=tmp_path / "background.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                mcp_tool_catalog_cache=MCPToolCatalogCache(),
            )

        runtime = create()
        try:
            first = [event async for event in runtime.stream("start background resource")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            assert servers.operations == [("ready-0", "resources/list")]
            assert not servers.clients[0].is_closed
            if reopen:
                thread = runtime.thread_id
                await runtime.aclose()
                assert servers.clients[0].is_closed
                runtime = create(thread)
            second = [event async for event in runtime.stream("observe background resource")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert servers.operations == [("ready-0", "resources/list")]
            assert servers.clients[0].is_closed
        finally:
            servers.operation_gate.set()
            await runtime.aclose()
        assert all(client.is_closed for client in servers.clients)
        assert not runtime._mcp_manager._resource_bindings

    asyncio.run(scenario())
