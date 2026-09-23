import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure, ToolSpec
from corki.tools import ToolRegistry
from corki.tools import search as search_module


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("changed", [True, False])
def test_mcp_admission_refresh_does_not_change_current_search_handler_corpus(
    tmp_path, monkeypatch, mode, changed
):
    async def scenario():
        clients, requests, executed, search_handlers = [], [], [], []

        def factory(settings):
            word = "cobalt" if clients and changed else "amber"

            def respond(request):
                message = json.loads(request.content)
                method = message["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": PROTOCOL_VERSION,
                        "instructions": word,
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "lookup",
                                "description": word,
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                elif method == "resources/list":
                    result = {"resources": []}
                elif method == "tools/call":
                    executed.append(word)
                    result = {"content": [{"type": "text", "text": word}]}
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            async def stream(self, request):
                requests.append(request)
                search_handlers.append(registry.get("tool_search"))
                step, turn = len(requests), request.items[-1].turn_id
                if step == 1:
                    assert "- docs: amber\n" in search_handlers[-1].spec.description
                    runtime.request_mcp_refresh()
                    # The exclusive MCP resource call consumes dirty before the
                    # search is admitted. Its new directory must not rewrite the
                    # search handler already selected by this model step.
                    calls = (
                        ToolCall(new_tool_call_id(), "list_mcp_resources", {}),
                        ToolCall(new_tool_call_id(), "tool_search", {"query": "amber"}),
                    )
                elif step == 2:
                    assert len(clients) == 2
                    assert search_handlers[-1] is not search_handlers[0]
                    raw = await runtime._repository.load_items(runtime._thread_id)
                    old = next(
                        i
                        for i in raw
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert old.discovered_tools[0].source_description == "amber"
                    visible = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert bool(visible.discovered_tools) == (not changed)
                    word = "cobalt" if changed else "amber"
                    calls = (ToolCall(new_tool_call_id(), "tool_search", {"query": word}),)
                elif step == 3:
                    assert search_handlers[-1] is search_handlers[1]
                    result = [
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    ][-1]
                    assert result.discovered_tools[0].source_description == (
                        "cobalt" if changed else "amber"
                    )
                    calls = (ToolCall(new_tool_call_id(), "mcp__docs::lookup", {}),)
                else:
                    assert step == 4 and executed == ["cobalt" if changed else "amber"]
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                step_id = new_step_id()
                yield ModelCompleted(tuple(ToolCallItem(call, turn, step_id) for call in calls))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                mcp_servers=(MCPServerSettings("docs", "http", url="https://docs.test/mcp"),),
            ),
            database_path=tmp_path / "cache.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("refresh during a tool batch")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 4
        finally:
            await runtime.aclose()
        assert all(client._client.is_closed for client in clients)

    asyncio.run(scenario())


def test_eager_index_build_failure_does_not_publish_and_startup_can_retry(tmp_path, monkeypatch):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Tool:
            spec = ToolSpec("lookup", "amber", {}, exposure=ToolExposure.DEFERRED)

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "failure.db",
            registry=registry,
            model=Model(),
        )
        try:
            with monkeypatch.context() as patch:

                def fail(text):
                    raise ValueError("fixture index build failed")

                patch.setattr("corki.tools.search._tokens", fail)
                for _ in range(2):
                    with pytest.raises(ValueError, match="fixture index build failed"):
                        await runtime._ensure_ready()
                    assert registry.get("tool_search") is None and runtime._compiled is None
                    assert not requests
            events = [e async for e in runtime.stream("retry after initialization failure")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1 and registry.get("tool_search").index.generation == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_refresh_index_failure_stops_before_next_sample_and_preserves_old_handler(
    tmp_path, monkeypatch
):
    async def scenario():
        requests, old_handlers = [], []
        registry = ToolRegistry()
        owner = registry.create_owner()

        class Tool:
            def __init__(self, word):
                self.spec = ToolSpec("lookup", word, {}, exposure=ToolExposure.DEFERRED)

        registry.replace_owned(owner, (Tool("amber"),))

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    old_handlers.append(registry.get("tool_search"))
                    registry.replace_owned(owner, (Tool("cobalt"),))
                    yield ModelCompleted(
                        (AssistantMessageItem("continue", turn, step),), end_turn=False
                    )
                elif len(requests) == 2:
                    assert registry.get("tool_search") is not old_handlers[0]
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "tool_search", {"query": "cobalt"}),
                                turn,
                                step,
                            ),
                        )
                    )
                else:
                    found = [
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    ][-1]
                    assert found.discovered_tools[0].description == "cobalt"
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "refresh-failure.db",
            registry=registry,
            model=Model(),
        )
        tokenize = search_module._tokens

        def fail(text):
            if "cobalt" in text:
                raise ValueError("fixture refresh index failed")
            return tokenize(text)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(search_module, "_tokens", fail)
                events = [e async for e in runtime.stream("refresh")]
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert "fixture refresh index failed" in events[-1].error
                assert len(requests) == 1
                assert registry.get("tool_search") is old_handlers[0]
                assert old_handlers[0].index.specs[0].description == "amber"
            events = [e async for e in runtime.stream("retry")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
