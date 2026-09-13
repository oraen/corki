"""Raw startup contracts determine real catalog publication and deferred search."""

import asyncio
import json
import sqlite3

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible", "code_mode"])
@pytest.mark.parametrize("carrier", ["json", "sse"])
@pytest.mark.parametrize("variant", ["sequence", "enum", "duplicate", "discover", "cache_value"])
def test_handshake_controls_search_catalog_and_cold_history(
    tmp_path, monkeypatch, mode, carrier, variant
):
    async def scenario():
        methods, calls, requests, clients = [], [], [], []
        invalid = variant in ("duplicate", "discover")
        selected = "healthy" if invalid else "subject"
        name = f"mcp__{selected}::read"
        cache = MCPToolCatalogCache()

        def factory(settings):
            def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                methods.append((settings.name, method))
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    info = '{"name":"fixture","version":"1"}'
                    capabilities = "{}"
                    if settings.name == "subject":
                        if variant == "sequence":
                            info = '["fixture",null,"1",null,null,null]'
                        if variant == "enum":
                            info = (
                                '{"name":"fixture","version":"1",'
                                '"icons":[{"src":"urn:x","theme":{"dark":null}}]}'
                            )
                        if variant == "duplicate":
                            info = '{"name":"PRIVATE","name":"fixture","version":"1"}'
                        if variant == "cache_value":
                            capabilities = (
                                '{"experimental":{"codex/tool-catalog-cache":'
                                '{"cacheable":{"$serde_json::private::RawValue":"false"}}}}'
                            )
                    result = (
                        '{"protocolVersion":"2025-06-18","capabilities":'
                        + capabilities
                        + ',"serverInfo":'
                        + info
                        + "}"
                    )
                    if settings.name == "subject" and variant == "discover":
                        result = (
                            result[:-1]
                            + ',"resultType":"complete","supportedVersions":[],"ttlMs":1,'
                            '"cacheScope":"public"}'
                        )
                elif method == "tools/list":
                    result = json.dumps(
                        {
                            "tools": [
                                {
                                    "name": "read",
                                    "description": "needle_" + settings.name,
                                    "inputSchema": {"type": "object"},
                                }
                            ]
                        }
                    )
                else:
                    assert method == "tools/call" and settings.name == selected
                    calls.append(packet)
                    result = '{"content":[{"type":"text","text":"selected result"}]}'
                body = '{"jsonrpc":"2.0","id":' + str(packet["id"]) + ',"result":' + result + "}"
                return httpx.Response(
                    200,
                    headers={
                        "content-type": "application/json"
                        if carrier == "json"
                        else "text/event-stream"
                    },
                    content=body if carrier == "json" else "data: " + body + "\n\n",
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        nested = mode == "code_mode"
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode="disabled" if nested else mode,
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            tool_mode="code_mode" if nested else "direct",
            mcp_servers=tuple(
                MCPServerSettings(n, "http", url=f"https://{n}.invalid/mcp")
                for n in ("subject", "healthy")
            ),
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert "PRIVATE" not in repr(request)
                assert (runtime._registry.get("mcp__subject::read") is None) is invalid
                if invalid:
                    assert ("subject", "tools/list") not in methods
                    assert ("subject", "notifications/initialized") not in methods
                    assert all(c.is_closed for c in clients if c.settings.name == "subject")
                    assert runtime._mcp_manager.warnings
                if variant == "cache_value":
                    assert cache.context(settings.mcp_servers[0]).current_tools_or() is None
                turn, step = request.items[-1].turn_id, new_step_id()
                count = len(requests)
                if not nested and count == 1:
                    assert name not in [tool.name for tool in request.tools]
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "needle_" + selected}
                    )
                elif count == (1 if nested else 2):
                    if not nested:
                        search = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        assert name in [tool.name for tool in search.discovered_tools]
                        if invalid:
                            assert "mcp__subject::read" not in [
                                tool.name for tool in search.discovered_tools
                            ]
                    call = (
                        ToolCall(new_tool_call_id(), name, {})
                        if not nested
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.mcp__{selected}__read({{}}));",
                        )
                    )
                else:
                    observation = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "selected result" in observation.content
                    assert len(calls) == 1
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        database = tmp_path / "startup.db"

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=database,
                home_path=tmp_path / "home",
                thread_id=thread,
                mcp_tool_catalog_cache=cache,
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            events = [event async for event in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await runtime._repository.load_items(thread)
            with sqlite3.connect(database) as db:
                ledger = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
                ).fetchall()
            assert len(ledger) == 1
            assert not json.loads(ledger[0][0])["is_error"]
        finally:
            await runtime.aclose()
        runtime = create(thread)
        try:
            events = [event async for event in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(calls) == 1
            restored = await runtime._repository.load_items(thread)
            assert restored[: len(history)] == history
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
                    ).fetchall()
                    == ledger
                )
        finally:
            await runtime.aclose()
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())
