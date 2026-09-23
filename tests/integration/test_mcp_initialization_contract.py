import asyncio
import json
import sqlite3

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ToolCallCompleted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible", "code_mode"])
@pytest.mark.parametrize("failure", ["schema", "sse", "notification"])
def test_invalid_handshake_isolated_before_catalog_with_runtime_cold_history(
    tmp_path, monkeypatch, mode, failure
):
    async def scenario():
        methods, calls, clients = [], [], []

        def factory(settings):
            def handle(request):
                message = json.loads(request.content)
                method = message["method"]
                methods.append((settings.name, method))
                broken = settings.name == "broken"
                if method == "notifications/initialized":
                    return (
                        httpx.Response(200, json={"invalid": "PRIVATE"})
                        if broken and failure == "notification"
                        else httpx.Response(202)
                    )
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                    if broken and failure == "schema":
                        result["capabilities"] = {"tools": {"listChanged": "PRIVATE"}}
                    if broken and failure == "sse":
                        packet = {"jsonrpc": "2.0", "id": message["id"], "result": result}
                        return httpx.Response(
                            200,
                            headers={"content-type": "text/event-stream"},
                            text="data: {PRIVATE broken}\n\ndata: " + json.dumps(packet) + "\n\n",
                        )
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "read",
                                "description": "needle",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call" and not broken
                    calls.append(message)
                    result = {"content": [{"type": "text", "text": "healthy result"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
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
                MCPServerSettings(n, "http", url=f"https://{n}.invalid", timeout_seconds=0.3)
                for n in ("broken", "healthy")
            ),
        )
        database = tmp_path / "history.db"

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                assert runtime._registry.get("mcp__broken::read") is None
                assert ("broken", "tools/list") not in methods
                assert all(c.is_closed for c in clients if c.settings.name == "broken")
                assert runtime._mcp_manager.warnings and all(
                    "PRIVATE" not in w for w in runtime._mcp_manager.warnings
                )
                turn, step = request.items[-1].turn_id, new_step_id()
                if not nested and self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == (1 if nested else 2):
                    if not nested:
                        search = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        assert [t.name for t in search.discovered_tools] == ["mcp__healthy::read"]
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="text(await tools.mcp__healthy__read({}));",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), "mcp__healthy::read", {})
                    )
                else:
                    observation = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "healthy result" in observation.content and len(calls) == 1
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        async def create(model, thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=model,
                registry=ToolRegistry(),
                database_path=database,
                home_path=tmp_path / "home",
                thread_id=thread,
            )

        runtime = await create(Model())
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            completions = [
                e
                for e in events
                if isinstance(e, ToolCallCompleted) and e.tool_name == "mcp__healthy::read"
            ]
            assert len(completions) == len(calls) == 1 and not completions[0].is_error
            raw = await runtime._repository.load_items(runtime._thread_id)
            with sqlite3.connect(database) as db:
                rows = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__healthy::read'"
                ).fetchall()
            assert len(rows) == 1
        finally:
            await runtime.aclose()

        class Cold:
            async def stream(self, request):
                assert len(calls) == 1
                yield ModelCompleted(
                    (AssistantMessageItem("cold", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        cold = await create(Cold(), runtime._thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(isinstance(e, ToolCallCompleted) for e in events)
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw and len(calls) == 1
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT result_json FROM tool_executions "
                        "WHERE tool_name='mcp__healthy::read'"
                    ).fetchall()
                    == rows
                )
        finally:
            await cold.aclose()
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())
