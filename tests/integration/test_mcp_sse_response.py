import asyncio
import json
import sqlite3

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import ToolCallCompleted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible", "code_mode"])
@pytest.mark.parametrize(
    "outcome",
    ["complete", "truncated", "read_failure", "resume_eof", "resume_failure", "resume_timeout"],
)
def test_streamed_mcp_search_call_observation_and_cold_no_replay(
    tmp_path, monkeypatch, mode, outcome
):
    async def scenario():
        calls, clients, opened, closed, gets = [], [], [], [], []
        pending = {}
        success = outcome in ("complete", "resume_eof", "resume_failure")

        class Stream(httpx.AsyncByteStream):
            def __init__(self, packet, kind):
                self.packet, self.kind = packet, kind
                opened.append(self)

            async def __aiter__(self):
                identity = self.packet["id"]
                if self.kind.startswith("resume_"):
                    yield f"id: cursor-{identity}\nretry: 0\nevent: ping\n\n".encode()
                    if self.kind == "resume_failure":
                        raise httpx.ReadError("interrupted resumable response")
                    return
                prefix = (
                    "event: ping\ndata: "
                    + json.dumps({"id": identity, "result": {"wrong": True}})
                    + "\n\ndata: {bad-json}\n\n"
                    + "data: "
                    + json.dumps({"id": identity, "method": "roots/list"})
                    + "\n\n"
                )
                if self.kind == "initialize":
                    # Initialize has different first-response/invalid-JSON rules.
                    prefix = ": initializing\n\n"
                payload = (
                    prefix.encode()
                    + b"data: "
                    + json.dumps(self.packet, ensure_ascii=False).encode()
                )
                if self.kind != "truncated":
                    payload += b"\r\n\r\n"
                if self.kind == "read_failure":
                    yield b"data: {"
                    raise httpx.ReadError("stream interrupted")
                for offset in range(0, len(payload), 7):
                    yield payload[offset : offset + 7]
                if self.kind == "complete":
                    await asyncio.Event().wait()

            async def aclose(self):
                closed.append(self)

        def factory(settings):
            def handle(request):
                if request.method == "GET":
                    gets.append(request)
                    assert opened == closed
                    assert request.headers["last-event-id"] in pending
                    if outcome == "resume_timeout":
                        return httpx.Response(404)
                    return httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        stream=Stream(pending[request.headers["last-event-id"]], "complete"),
                    )
                message = json.loads(request.content)
                if "id" not in message:
                    return httpx.Response(202)
                kind = "complete"
                if message["method"] == "initialize":
                    kind = "initialize"
                    result = {
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                    }
                elif message["method"] == "tools/list":
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
                    assert message["method"] == "tools/call"
                    calls.append(message)
                    kind = outcome
                    result = {"content": [{"type": "text", "text": "流式结果"}]}
                packet = {"jsonrpc": "2.0", "id": str(message["id"]), "result": result}
                pending[f"cursor-{packet['id']}"] = packet
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=Stream(packet, kind)
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        name, nested = "mcp__docs::read", mode == "code_mode"

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if not nested and self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == (1 if nested else 2):
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="text(await tools.mcp__docs__read({}));",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), name, {})
                    )
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if not nested:
                        assert result.is_error == (not success)
                    assert ("流式结果" in result.content) == success
                    assert "wrong" not in result.content
                    assert len(calls) == 1
                    yield ModelCompleted((AssistantMessageItem("handled", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode="disabled" if nested else mode,
            tool_mode="code_mode" if nested else "direct",
            mcp_servers=(
                MCPServerSettings(
                    "docs", "http", url="https://fixture.invalid", timeout_seconds=0.3
                ),
            ),
        )
        database = tmp_path / "history.db"

        def create(model, thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                model=model,
                registry=ToolRegistry(),
                database_path=database,
                home_path=tmp_path / "home",
                thread_id=thread,
            )

        runtime = create(Model())
        try:
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            completions = [
                e for e in events if isinstance(e, ToolCallCompleted) and e.tool_name == name
            ]
            assert len(completions) == len(calls) == 1
            assert completions[0].is_error == (not success)
            assert (completions[0].mcp_result_json is not None) == success
            assert (completions[0].mcp_error is not None) == (not success)
            assert len(gets) == int(outcome.startswith("resume_"))
            assert opened == closed
            raw = await runtime._repository.load_items(runtime._thread_id)
            with sqlite3.connect(database) as db:
                rows = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
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

        cold = create(Cold(), runtime._thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(isinstance(e, ToolCallCompleted) for e in events)
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw and len(calls) == 1
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
                    ).fetchall()
                    == rows
                )
        finally:
            await cold.aclose()
        assert all(c.is_closed for c in clients) and opened == closed

    asyncio.run(scenario())
