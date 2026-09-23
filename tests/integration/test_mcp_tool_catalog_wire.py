"""Typed legacy catalogs must reach discovery, execution and durable history."""

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

CASES = [
    (mode, carrier, variant)
    for mode in ("native", "compatible", "code_mode")
    for carrier in ("json", "sse")
    for variant in (
        "sequence",
        "schema_value",
        "cursor",
        "duplicate",
        "bad_member",
        "wrong_result",
        "2049",
    )
] + [(mode, "json", count) for mode in ("native", "compatible") for count in ("1025", "2048")]


@pytest.mark.parametrize("mode,carrier,variant", CASES)
def test_raw_catalog_controls_search_call_and_cold_history(
    tmp_path, monkeypatch, mode, carrier, variant
):
    async def scenario():
        methods, calls, requests, clients = [], [], [], []
        invalid = variant in ("duplicate", "bad_member", "wrong_result", "2049")
        selected = "healthy" if invalid else "subject"
        name = f"mcp__{selected}::read"
        cache = MCPToolCatalogCache()
        schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

        def factory(server):
            listings = []

            def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                methods.append((server.name, method))
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = json.dumps(
                        {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "serverInfo": {"name": "fixture", "version": "1"},
                        }
                    )
                elif method == "tools/list":
                    listings.append(packet)
                    assert len(listings) == 1, "legacy catalog must not follow nextCursor"
                    assert "cursor" not in packet["params"]
                    tool = {
                        "name": "read",
                        "description": "needle_" + server.name,
                        "inputSchema": schema,
                    }
                    tools = [tool]
                    if server.name == "subject" and variant.isdecimal():
                        tools = [
                            {"name": f"unrelated_{i}", "inputSchema": {"type": "object"}}
                            for i in range(int(variant) - 1)
                        ] + [tool]
                    result = json.dumps({"tools": tools})
                    if server.name == "subject":
                        if variant == "sequence":
                            result = json.dumps(
                                {
                                    "tools": [
                                        [
                                            "read",
                                            None,
                                            "needle_subject",
                                            schema,
                                            None,
                                            [None, True, None, None, None],
                                            [["urn:x", None, None, {"dark": None}]],
                                            None,
                                        ]
                                    ]
                                }
                            )
                        elif variant == "schema_value":
                            tool["inputSchema"] = {
                                **schema,
                                "type": {"$serde_json::private::RawValue": '"object"'},
                            }
                            result = json.dumps({"tools": [tool]})
                        elif variant == "cursor":
                            result = json.dumps({"tools": [tool], "nextCursor": "second"})
                        elif variant == "duplicate":
                            result = '{"tools":null,"tools":[' + json.dumps(tool) + "]}"
                        elif variant == "bad_member":
                            result = json.dumps({"tools": [tool, 7]})
                        elif variant == "wrong_result":
                            result = json.dumps({"tools": [tool], "completion": {"values": []}})
                else:
                    assert method == "tools/call" and server.name == selected
                    assert packet["params"]["name"] == "read"
                    assert packet["params"]["arguments"] == {"value": "ok"}
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

            client = HttpMCPClient(server, transport=httpx.MockTransport(respond))
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
                assert (runtime._registry.get("mcp__subject::read") is None) is invalid
                if invalid:
                    assert runtime._mcp_manager.warnings
                    assert cache.context(settings.mcp_servers[0]).current_tools_or() is None
                    assert not any(t.name.startswith("mcp__subject::") for t in request.tools)
                    assert all(c.is_closed for c in clients if c.settings.name == "subject")
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
                        discovered = {tool.name: tool for tool in search.discovered_tools}
                        assert name in discovered
                        assert discovered[name].parameters == schema
                        assert not invalid or not any(
                            n.startswith("mcp__subject::") for n in discovered
                        )
                    call = (
                        ToolCall(new_tool_call_id(), name, {"value": "ok"})
                        if not nested
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=(
                                f'text(await tools.mcp__{selected}__read({{value:"ok"}}));'
                            ),
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

        database = tmp_path / "catalog.db"

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=database,
                home_path=tmp_path / "home",
                thread_id=thread,
                mcp_tool_catalog_cache=cache,
            )

        runtime = await create()
        thread = runtime.thread_id
        try:
            events = [event async for event in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert methods.count(("subject", "tools/list")) == 1
            assert methods.count(("subject", "notifications/initialized")) == 1
            history = await runtime._repository.load_items(thread)
            with sqlite3.connect(database) as db:
                ledger = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
                ).fetchall()
            assert len(ledger) == 1
            assert not json.loads(ledger[0][0])["is_error"]
        finally:
            await runtime.aclose()
        runtime = await create(thread)
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
