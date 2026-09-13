"""Actual Runtime discovery, calls, isolated schema failures and Agent exposure."""

import asyncio

import pytest
from test_code_mode_results import run_script
from test_mcp_pending_reuse import PendingClient

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings, MCPServerSettings
from corki.config.model_context import ModelContextInfo
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.manager import MCPManager
from corki.mcp.names import normalize_tool_names
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.mcp.tools import MCPTool
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure
from corki.tools import ToolRegistry


def install_client(monkeypatch, definitions):
    clients = []

    class Client(PendingClient):
        async def list_tools(self):
            self.lists += 1
            return definitions

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"content": [{"type": "text", "text": "accepted"}]}

    def factory(settings):
        client = Client(settings, "unused")
        clients.append(client)
        return client

    monkeypatch.setattr("corki.mcp.manager.create_client", factory)
    monkeypatch.setattr(
        "corki.mcp.manager.HttpMCPClient", lambda settings, **kwargs: factory(settings)
    )
    return clients


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("agent", [False, True])
def test_search_load_call_observation_uses_normalized_schema_not_argument_authority(
    tmp_path, monkeypatch, mode, agent
):
    async def scenario():
        raw = {
            "properties": {f"field_{i}": {"type": "string"} for i in range(1024)},
            "required": ["field_0"],
            "additionalProperties": False,
        }
        clients = install_client(
            monkeypatch, [{"name": "lookup", "description": "Locate documents", "inputSchema": raw}]
        )
        settings = MCPServerSettings("docs", "http", url="https://schema.test", required=True)
        source = MCPCatalogSource("plugin", "fixture", agent_plugin=agent)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                phase = (len(requests) - 1) % 3
                turn, step = request.items[-1].turn_id, new_step_id()
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                if phase == 0:
                    if len(requests) == 1:
                        assert not any(t.name.startswith("mcp__docs") for t in request.tools)
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "Locate documents"}
                    )
                elif phase == 1:
                    (spec,) = results[-1].discovered_tools
                    assert spec.name == "mcp__docs::lookup"
                    if agent:
                        assert spec.parameters == {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        }
                    else:
                        assert spec.parameters == {"type": "object", **raw}
                    call = (
                        ToolCall(new_tool_call_id(), spec.name, {"unadvertised": True})
                        if len(requests) == 2
                        else ToolCall(new_tool_call_id(), spec.name, None, raw_arguments=" \n")
                    )
                else:
                    assert not results[-1].is_error and "accepted" in results[-1].content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            ),
            mcp_catalog=MCPCatalog((MCPRegistration(settings, source),)),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "schema.db",
        )
        try:
            for _ in range(2):
                events = [
                    event async for event in runtime.stream("Find and call the document tool")
                ]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 6
            assert len(clients) == 1 and clients[0].starts == clients[0].lists == 1
            assert clients[0].calls == [("lookup", {"unadvertised": True}), ("lookup", None)]
            assert "type" not in raw
        finally:
            await runtime.aclose()
        assert clients[0].closes == 1

    asyncio.run(scenario())


def test_invalid_schema_skips_only_one_tool_in_live_and_cached_catalog(monkeypatch):
    async def scenario():
        definitions = [
            {"name": "read-a", "inputSchema": {"type": "object", "required": False}},
            {"name": "read_a", "inputSchema": {"properties": {"x": True}}},
        ]
        clients = []

        class Client(PendingClient):
            async def list_tools(self):
                self.lists += 1
                await self.pause("list")
                return definitions

        def factory(settings):
            client = Client(settings, "list")
            if not clients:
                client.release.set()
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        settings = MCPServerSettings("docs", "http", url="https://schema.test")
        cache, registries = MCPToolCatalogCache(), [ToolRegistry(), ToolRegistry()]
        managers = [
            MCPManager((settings,), registry, tool_catalog_cache=cache) for registry in registries
        ]
        expected = next(
            name.canonical
            for name in normalize_tool_names(("docs", d["name"]) for d in definitions)
            if name.remote == "read_a"
        )
        try:
            await managers[0].start()
            await managers[1].capture_tools(optional_startup_grace_ms=1)
            for manager, registry in zip(managers, registries, strict=True):
                tools = [t for t in registry.specs() if t.name.startswith("mcp__docs")]
                assert len(tools) == 1 and tools[0].name == expected
                assert tools[0].parameters == {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                }
                assert any(
                    "Skipping MCP tool docs/read-a" in warning for warning in manager.warnings
                )
            assert not clients[1].release.is_set() and clients[0].closes == 0
            assert cache.context(settings).current_tools_or().definitions == tuple(definitions)
        finally:
            for manager in managers:
                await manager.aclose()
        assert all(client.closes == 1 for client in clients)

    asyncio.run(scenario())


def test_agent_total_budget_hides_tool_from_model_dispatch(tmp_path, monkeypatch):
    async def scenario():
        definitions = [
            {
                "name": f"t_{i:02d}",
                "inputSchema": {"properties": {"x": {"type": "string", "enum": ["x" * 7000]}}},
            }
            for i in range(12)
        ]
        clients = install_client(monkeypatch, definitions)
        settings = MCPServerSettings("docs", "http", url="https://schema.test", required=True)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                names = {spec.name for spec in request.tools if spec.name.startswith("mcp__docs")}
                assert names == {f"mcp__docs::t_{i:02d}" for i in range(8)}
                if len(requests) == 1:
                    call = ToolCall(new_tool_call_id(), "mcp__docs::t_08", {})
                elif len(requests) == 2:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert result.is_error and not clients[0].calls
                    call = ToolCall(new_tool_call_id(), "mcp__docs::t_00", {})
                else:
                    assert clients[0].calls == [("t_00", {})]
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_search_mode="disabled",
            ),
            mcp_catalog=MCPCatalog(
                (
                    MCPRegistration(
                        settings, MCPCatalogSource("plugin", "fixture", agent_plugin=True)
                    ),
                )
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "budget.db",
        )
        try:
            events = [event async for event in runtime.stream("Call the available tools")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3
            assert (
                runtime._registry.snapshot().spec("mcp__docs::t_08").exposure == ToolExposure.HIDDEN
            )
        finally:
            await runtime.aclose()
        assert clients[0].closes == 1

    asyncio.run(scenario())


@pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")
@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
def test_code_mode_object_boundary_and_omitted_input_are_distinct_from_direct_mcp(tmp_path, mode):
    calls = []

    class Client:
        async def call_tool(self, name, arguments):
            calls.append(arguments)
            return {"content": [], "structuredContent": {"accepted": True}}

    registry = ToolRegistry()
    registry.register(
        MCPTool(
            "docs",
            {
                "name": "read",
                "inputSchema": {"type": "object", "required": ["unused"]},
            },
            Client(),
        )
    )
    content, _, _ = asyncio.run(
        run_script(
            tmp_path,
            "try { await tools.mcp__docs__read([]); } catch (e) { text('rejected non-object'); } "
            "text(await tools.mcp__docs__read());",
            registry=registry,
            mode=mode,
        )
    )
    assert "rejected non-object" in content and '"accepted":true' in content
    assert calls == [{}]  # Code Mode supplies {}, whereas direct whitespace omits arguments.
