"""MCP memory policy uses real call admission, not model-supplied names/metadata."""

import asyncio
import json
import sqlite3

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "case",
    [
        "fake",
        "invalid_json",
        "invalid_schema",
        "unadvertised",
        "removed_server",
        "removed_tool",
        "valid",
        "remote_error",
        "timeout",
    ],
)
def test_mcp_pollution_requires_actual_admitted_call(tmp_path, monkeypatch, case):
    async def scenario():
        database = tmp_path / "sessions.db"
        sent = []
        empty = False
        should_mark = case in {"valid", "remote_error", "timeout"}

        def memory_mode():
            with sqlite3.connect(database) as db:
                return db.execute("SELECT memory_mode FROM threads").fetchone()[0]

        def factory(settings):
            def handler(request):
                if request.method == "DELETE":
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}}
                elif method == "tools/list":
                    result = {
                        "tools": []
                        if empty
                        else [
                            {
                                "name": "read",
                                "description": "read fixture",
                                "inputSchema": {
                                    "type": "object",
                                    "required": ["value"],
                                    "properties": {"value": {"type": "string"}},
                                },
                                "annotations": {"readOnlyHint": True, "pollutes_memory": False},
                                "_meta": {"pollutes_memory": False},
                            }
                        ]
                    }
                elif method == "tools/call":
                    sent.append(message)
                    assert memory_mode() == "polluted", "mark must precede remote execution"
                    if case == "timeout":
                        raise httpx.ReadTimeout("fixture timeout", request=request)
                    result = {
                        "content": [{"type": "text", "text": "external"}],
                        "isError": case == "remote_error",
                    }
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
                )

            return HttpMCPClient(settings, transport=httpx.MockTransport(handler))

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()

        class Fake:
            spec = ToolSpec("mcp__fake__read", "Local tool with MCP-like name", {"type": "object"})

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "local")

        registry.register(Fake())

        class Model:
            count = 0

            async def stream(self, request):
                nonlocal empty
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    if case == "removed_server":
                        runtime.request_mcp_refresh(())
                    elif case == "removed_tool":
                        empty = True
                        runtime.request_mcp_refresh()
                    name = "mcp__fake__read" if case == "fake" else "mcp__docs__read"
                    call = ToolCall(
                        new_tool_call_id(), name, {"value": 3 if case == "invalid_schema" else "ok"}
                    )
                    if case == "invalid_json":
                        call = ToolCall(call.id, name, None, "{", parse_error="invalid JSON")
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert result.is_error == (case not in {"fake", "valid"})
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_disable_on_external_context=True,
                tool_search_mode="compatible" if case == "unadvertised" else "disabled",
                mcp_servers=(MCPServerSettings("docs", "http", url="https://fixture.test/mcp"),),
            ),
            database_path=database,
            registry=registry,
            model=Model(),
            memory_root=tmp_path / "memories",
        )
        try:
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(sent) == int(should_mark)
            assert memory_mode() == ("polluted" if should_mark else "enabled")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "initial,latest", [(True, True), (True, False), (False, True), (False, False)]
)
@pytest.mark.parametrize("code_mode", [False, True])
def test_latest_host_metadata_controls_direct_and_nested_mcp(
    tmp_path, monkeypatch, initial, latest, code_mode
):
    from corki.mcp import MCPServerMetadata

    async def scenario():
        database = tmp_path / "sessions.db"
        clients, calls = [], []

        class Client:
            server_instructions = None

            def __init__(self, settings):
                self.settings = settings
                self.generation = len(clients)
                clients.append(self)

            async def start(self):
                pass

            async def list_tools(self):
                return [
                    {
                        "name": "read",
                        "inputSchema": {"type": "object"},
                        "_meta": {"pollutes_memory": not latest},
                    }
                ]

            async def call_tool(self, name, arguments):
                with sqlite3.connect(database) as db:
                    assert db.execute("SELECT memory_mode FROM threads").fetchone()[0] == (
                        "polluted" if latest else "enabled"
                    )
                calls.append(self.generation)
                return {"content": [{"type": "text", "text": "current generation"}]}

            async def aclose(self):
                pass

        monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    runtime.request_mcp_refresh(server_metadata={"docs": MCPServerMetadata(latest)})
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            "text(await tools.mcp__docs__read({}));",
                            input_kind="freeform",
                        )
                        if code_mode
                        else ToolCall(new_tool_call_id(), "mcp__docs__read", {})
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert not result.is_error and "current generation" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        metadata = {"docs": MCPServerMetadata(initial)}
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_disable_on_external_context=True,
                tool_mode="code_mode" if code_mode else "direct",
                tool_search_mode="disabled",
                mcp_servers=(MCPServerSettings("docs", "http", url="https://fixture.test/mcp"),),
            ),
            database_path=database,
            registry=ToolRegistry(),
            model=Model(),
            memory_root=tmp_path / "memories",
            mcp_server_metadata=metadata,
        )
        metadata.clear()  # Mutating the caller's mapping must not mutate host state.
        try:
            assert isinstance([e async for e in runtime.stream("read")][-1], TurnCompleted)
            assert calls == [1] and len(clients) == 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cancellation_before_mcp_dispatch_does_not_mark_thread(tmp_path):
    from corki.mcp.tools import MCPTool
    from corki.protocol.events import TurnCancelled

    async def scenario():
        class Client:
            async def call_tool(self, name, arguments):
                raise AssertionError("cancelled call must not run")

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), "mcp__docs__read", {}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(MCPTool("docs", {"name": "read"}, Client()))
        database = tmp_path / "sessions.db"
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_disable_on_external_context=True,
            ),
            database_path=database,
            registry=registry,
            model=Model(),
            memory_root=tmp_path / "memories",
        )

        async def cancel(*args):
            raise asyncio.CancelledError

        runtime._repository.claim_tool_call = cancel
        events = []
        try:
            with pytest.raises(asyncio.CancelledError):
                async for event in runtime.stream("read"):
                    events.append(event)
            assert isinstance(events[-1], TurnCancelled)
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT memory_mode FROM threads").fetchone()[0] == "enabled"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
