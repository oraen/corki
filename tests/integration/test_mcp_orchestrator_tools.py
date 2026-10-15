"""Legacy orchestrator flags do not filter ordinary servers or activate native search."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["direct", "compatible", "code_mode_only"])
@pytest.mark.parametrize("unprefixed", [False, True])
@pytest.mark.parametrize("reopen", [False, True])
def test_legacy_orchestrator_flag_preserves_ordinary_search_and_dispatch(
    tmp_path, monkeypatch, mode, unprefixed, reopen
):
    async def scenario():
        clients, calls, outputs = [], [], []
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            execution_permissions=None,
            orchestrator_mcp_enabled=False,
            tool_search_mode="compatible" if mode == "compatible" else "disabled",
            tool_mode="code_mode_only" if mode == "code_mode_only" else "direct",
            non_prefixed_mcp_tool_names=unprefixed,
            mcp_servers=tuple(
                MCPServerSettings(name, "http", url=f"https://{name}.invalid")
                for name in ("codex_apps", "ordinary")
            ),
        )

        def factory(server):
            async def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": server.name, "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "lookup_app_marker"
                                if server.name == "codex_apps"
                                else "lookup_normal",
                                "description": "lookup fixture records",
                                "_meta": {"connector_id": "fixture"},
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call"
                    calls.append(server.name)
                    result = {"content": [{"type": "text", "text": "called-" + server.name}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(server, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        def emit(request, name, args, nested=False):
            call = (
                ToolCall(new_tool_call_id(), name, args)
                if not nested
                else ToolCall(
                    new_tool_call_id(),
                    "exec",
                    None,
                    input_kind="freeform",
                    raw_arguments=(
                        f"text(await tools.{name.replace('::', '__')}({json.dumps(args)}));"
                    ),
                )
            )
            return ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))

        class WarmModel:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                if self.steps == 1:
                    yield emit(request, "tool_search", {"query": "lookup_app_marker"})
                elif self.steps == 2:
                    found = next(t.name for t in request.tools if "lookup_app_marker" in t.name)
                    yield emit(request, found, {})
                else:
                    yield ModelCompleted(
                        (AssistantMessageItem("warm", request.items[-1].turn_id, new_step_id()),)
                    )

            async def aclose(self):
                pass

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                full = runtime._registry.snapshot()
                app = next(
                    t.name
                    for t in await runtime._mcp_manager.list_tool_catalog()
                    if "lookup_app_marker" in t.name
                )
                assert app in {t.name for t in full.specs()}
                normal = next(t.name for t in full.specs() if "lookup_normal" in t.name)
                if mode == "direct" or mode == "compatible" and self.steps > 1:
                    assert app in {t.name for t in request.tools}
                if self.steps > 1:
                    outputs.append([i for i in request.items if isinstance(i, ToolResultItem)][-1])
                if mode == "compatible" and self.steps == 1:
                    yield emit(request, "tool_search", {"query": "lookup"})
                else:
                    step = self.steps - int(mode == "compatible")
                    if step <= 2:
                        yield emit(
                            request, normal if step == 1 else app, {}, mode == "code_mode_only"
                        )
                    else:
                        yield ModelCompleted(
                            (
                                AssistantMessageItem(
                                    "done", request.items[-1].turn_id, new_step_id()
                                ),
                            )
                        )

            async def aclose(self):
                pass

        async def create(model, selected=settings, thread=None):
            return await LangGraphRuntime.acreate(
                settings=selected,
                model=model,
                registry=ToolRegistry(),
                thread_id=thread,
                database_path=tmp_path / "tools.db",
                home_path=tmp_path / "home",
                mcp_tool_catalog_cache=MCPToolCatalogCache(),
            )

        thread = None
        warm_history = ()
        if reopen:
            warm = await create(
                WarmModel(),
                replace(
                    settings,
                    orchestrator_mcp_enabled=True,
                    tool_mode="direct",
                    tool_search_mode="compatible",
                ),
            )
            try:
                events = [event async for event in warm.stream("use Apps")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert calls == ["codex_apps"]
                thread = warm.thread_id
                warm_history = await warm._repository.load_items(thread)
            finally:
                await warm.aclose()
            calls.clear()
        runtime = await create(Model(), thread=thread)
        try:
            events = [event async for event in runtime.stream("call both ordinary MCP servers")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ["ordinary", "codex_apps"]
            history = await runtime._repository.load_items(runtime.thread_id)
            assert history[: len(warm_history)] == warm_history
            assert not outputs[-2].is_error and "called-ordinary" in outputs[-2].content
            assert not outputs[-1].is_error and "called-codex_apps" in outputs[-1].content
            if mode == "compatible":
                assert "lookup_normal" in outputs[0].content
                assert "lookup_app_marker" in outputs[0].content
            # Host inventory and direct calls are not mutated by the model router.
            await runtime._mcp_manager.call_tool("codex_apps", "lookup_app_marker", {})
            assert calls == ["ordinary", "codex_apps", "codex_apps"]
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())


def test_legacy_orchestrator_flag_cannot_enable_native_search(tmp_path, monkeypatch):
    async def scenario():
        bodies, calls, clients = [], [], []

        def respond(request):
            packet = json.loads(request.content)
            if "method" in packet:
                server = request.url.path.rsplit("/", 1)[-1]
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": server, "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "lookup",
                                "description": "lookup fixture",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call"
                    calls.append(server)
                    result = {"content": [{"type": "text", "text": "called-" + server}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )
            bodies.append(packet)
            step = len(bodies)
            assert "mcp__codex_apps" not in json.dumps(packet["tools"])
            assert step == 1, "unsupported native search must not retry or execute"
            item = {
                "type": "tool_search_call",
                "execution": "client",
                "arguments": {"query": "lookup"},
                "id": "search-item",
                "call_id": "search-call",
            }
            events = [
                {"type": "response.output_item.done", "item": item},
                {"type": "response.completed", "response": {"id": f"r-{step}", "output": [item]}},
            ]
            return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events))

        def factory(server):
            client = HttpMCPClient(server, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=http_client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
                supports_native_tool_search=True,
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                api_mode="responses",
                tool_search_mode="native",
                orchestrator_mcp_enabled=False,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                mcp_servers=tuple(
                    MCPServerSettings(name, "http", url=f"https://fixture.invalid/mcp/{name}")
                    for name in ("codex_apps", "ordinary")
                ),
            ),
            registry=ToolRegistry(),
            model=model,
            database_path=tmp_path / "native.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )
        try:
            events = [event async for event in runtime.stream("find lookup")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert events[-1].error_kind == "protocol" and not events[-1].retryable
            assert len(bodies) == 1 and calls == []
        finally:
            await runtime.aclose()
            await http_client.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())
