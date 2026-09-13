import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient, StdioMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ToolCallCompleted, TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible", "code_mode"])
@pytest.mark.parametrize(
    "transport,outcome",
    [("http", o) for o in ("result", "error", "timeout", "ping", "remote_cancel", "progress")]
    + [
        ("stdio", o)
        for o in ("ping", "remote_cancel", "eof", "progress", "disconnect", "partial_exit")
    ],
)
def test_common_stream_search_call_observation_ledger_and_cold_history(
    tmp_path, monkeypatch, caplog, mode, outcome, transport
):
    caplog.set_level(20, logger="corki.mcp.inbound")

    async def scenario():
        calls, streams, clients, deleted_after_close, replies = [], [], [], [], []
        processes = []

        class Stream(httpx.AsyncByteStream):
            def __init__(self):
                self.queue = asyncio.Queue()
                self.closed = False

            async def __aiter__(self):
                while True:
                    yield await self.queue.get()

            async def aclose(self):
                self.closed = True

        def factory(settings):
            if transport == "stdio":

                class TracedClient(StdioMCPClient):
                    async def _spawn(self, env):
                        await super()._spawn(env)
                        processes.append(self._process)
                        if outcome == "eof":
                            eof = asyncio.Event()
                            readline = self._process.stdout.readline
                            send_reply = self._inbound._send

                            async def read():
                                line = await readline()
                                if not line:
                                    eof.set()
                                return line

                            async def send_after_eof(message):
                                await eof.wait()
                                await send_reply(message)

                            # A real pipe half-close, with deterministic in-flight ordering.
                            self._process.stdout.readline = read
                            self._inbound._send = send_after_eof

                    async def call_tool(self, name, arguments):
                        calls.append((name, arguments))
                        return await super().call_tool(name, arguments)

                client = TracedClient(settings)
                clients.append(client)
                return client
            stream = Stream()
            streams.append(stream)

            def handle(request):
                if request.method == "GET":
                    assert request.headers["mcp-session-id"] == "session"
                    return httpx.Response(
                        200, headers={"content-type": "text/event-stream"}, stream=stream
                    )
                if request.method == "DELETE":
                    deleted_after_close.append(stream.closed)
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message.get("method")
                if method is None:
                    assert outcome == "ping"
                    assert message == {"jsonrpc": "2.0", "id": calls[-1]["id"], "result": {}}
                    replies.append(message)
                    result = {
                        "jsonrpc": "2.0",
                        "id": calls[-1]["id"],
                        "result": {"content": [{"type": "text", "text": "routed result"}]},
                    }
                    stream.queue.put_nowait(b"data: " + json.dumps(result).encode() + b"\n\n")
                    return httpx.Response(202)
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
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
                    assert method == "tools/call"
                    calls.append(message)
                    if outcome != "timeout":
                        packet = {"jsonrpc": "2.0", "id": message["id"]}
                        if outcome == "progress":
                            token = message["params"]["_meta"]["progressToken"]
                            assert type(token) is int
                            assert "_meta" not in message["params"]["arguments"]
                            notification = {
                                "jsonrpc": "2.0",
                                "method": "notifications/progress",
                                "params": {
                                    "progressToken": token,
                                    "progress": 1,
                                    "message": "fixture progress",
                                    "_meta": {"private": "PRIVATE_PROGRESS_META"},
                                },
                            }
                            stream.queue.put_nowait(
                                b"data: " + json.dumps(notification).encode() + b"\n\n"
                            )
                        if outcome == "ping":
                            packet["method"] = "ping"
                        elif outcome == "remote_cancel":
                            packet = {
                                "jsonrpc": "2.0",
                                "method": "notifications/cancelled",
                                "params": {"requestId": message["id"], "reason": "server stopped"},
                            }
                        elif outcome == "error":
                            packet["error"] = {"code": -32001, "message": "remote failure"}
                        else:
                            packet["result"] = {
                                "content": [{"type": "text", "text": "routed result"}]
                            }
                        stream.queue.put_nowait(b"data: " + json.dumps(packet).encode() + b"\n\n")
                    return httpx.Response(202)
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": "session"} if method == "initialize" else {},
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
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
            mcp_servers=(
                MCPServerSettings(
                    "fixture", "http", url="https://fixture.invalid", timeout_seconds=0.15
                )
                if transport == "http"
                else MCPServerSettings(
                    "fixture",
                    "stdio",
                    command=sys.executable,
                    args=(
                        "-u",
                        str(
                            Path(__file__).resolve().parents[1]
                            / "fixtures"
                            / "inbound_mcp_server.py"
                        ),
                        {
                            "ping": "ping",
                            "remote_cancel": "cancel",
                            "eof": "eof",
                            "progress": "progress",
                            "disconnect": "disconnect",
                            "partial_exit": "partial_exit",
                        }[outcome],
                    ),
                    timeout_seconds=0.3,
                ),
            ),
        )
        database = tmp_path / "history.db"

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if not nested and self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == (1 if nested else 2):
                    if not nested:
                        search = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        assert [t.name for t in search.discovered_tools] == ["mcp__fixture::read"]
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="text(await tools.mcp__fixture__read({}));",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), "mcp__fixture::read", {})
                    )
                else:
                    if outcome in {"disconnect", "partial_exit"}:
                        assert await asyncio.wait_for(processes[0].wait(), 1) == 23
                    if outcome == "eof":
                        # The actual half-closed server exits only after receiving our reply.
                        # Step refresh may already have closed the disconnected client
                        # and cleared its pointer. Keep checking the actual spawned child.
                        process = processes[0]
                        assert await asyncio.wait_for(process.wait(), 0.5) == 0
                    observation = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    expected = {
                        "result": "routed result",
                        "error": "remote failure",
                        "timeout": "timed out",
                        "ping": "routed result" if transport == "http" else "reverse RPC completed",
                        "remote_cancel": "server stopped",
                        "disconnect": "closed its output",
                        "partial_exit": "closed its output",
                        "eof": "reverse RPC completed",
                        "progress": "routed result"
                        if transport == "http"
                        else "reverse RPC completed",
                    }[outcome]
                    assert expected in observation.content and len(calls) == 1
                    assert "PRIVATE_PROGRESS_META" not in repr(request)
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

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
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            completions = [
                e
                for e in events
                if isinstance(e, ToolCallCompleted) and e.tool_name == "mcp__fixture::read"
            ]
            assert len(completions) == len(calls) == 1
            assert completions[0].is_error == (outcome not in ("result", "ping", "eof", "progress"))
            if outcome == "progress":
                assert "fixture progress" in caplog.text
                assert "PRIVATE_PROGRESS_META" not in caplog.text
            assert not any(isinstance(e, TurnCancelled) for e in events)
            if transport == "http":
                assert len(replies) == int(outcome == "ping")
            raw = await runtime._repository.load_items(runtime._thread_id)
            with sqlite3.connect(database) as db:
                rows = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__fixture::read'"
                ).fetchall()
            assert len(rows) == 1
        finally:
            await runtime.aclose()
        assert all(s.closed for s in streams) and all(deleted_after_close)

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
            assert (await cold._repository.load_items(cold._thread_id))[: len(raw)] == raw
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT result_json FROM tool_executions "
                        "WHERE tool_name='mcp__fixture::read'"
                    ).fetchall()
                    == rows
                )
        finally:
            await cold.aclose()
        assert (
            len(calls) == 1 and all(c.is_closed for c in clients) and all(s.closed for s in streams)
        )
        if transport == "stdio":
            assert all(p.returncode is not None for p in processes)
            assert all(c._reader_task.done() and c._stderr_task.done() for c in clients)
            assert all(not c._pending for c in clients)

    asyncio.run(asyncio.wait_for(scenario(), 15))
