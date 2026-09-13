import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient, StdioMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, TurnStarted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("phase", ["tools/list", "tools/call"])
@pytest.mark.parametrize("mode", ["native", "compatible", "code_mode"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_runtime_host_response_survives_human_wait(
    tmp_path,
    monkeypatch,
    transport,
    phase,
    mode,
    action,
    reply_delay=0.0,
    expect_timeout=False,
    request_budget=None,
):
    if request_budget is None:
        # Listing also exercises the bounded optional startup grace. Do not
        # turn this fixture into an assumption that a late catalog is ready.
        request_budget = 0.2 if phase == "tools/list" else 1.0

    async def scenario():
        requests, replies, clients, methods = [], [], [], []
        received, release = asyncio.Event(), asyncio.Event()
        queue = asyncio.Queue()
        original = None

        def result_for(method):
            if method == "tools/list":
                return {
                    "tools": [
                        {"name": "read", "description": "needle", "inputSchema": {"type": "object"}}
                    ]
                }
            return {"content": [{"type": "text", "text": "host response received"}]}

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                while True:
                    yield await queue.get()

        async def handle(request):
            nonlocal original
            if request.method == "GET":
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=Stream()
                )
            if request.method == "DELETE":
                return httpx.Response(204)
            message = json.loads(request.content)
            method = message.get("method")
            if method is not None:
                methods.append(method)
            if method is None:
                replies.append(message)
                if reply_delay:
                    assert clients[0].active_time._pauses == 0
                    await asyncio.sleep(reply_delay)
                queue.put_nowait(
                    b"data: "
                    + json.dumps(
                        {"jsonrpc": "2.0", "id": original, "result": result_for(phase)}
                    ).encode()
                    + b"\n\n"
                )
                return httpx.Response(202)
            if method == "notifications/initialized":
                return httpx.Response(202)
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif method == phase:
                original = message["id"]
                queue.put_nowait(
                    b"data: "
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "server-private-id",
                            "method": "elicitation/create",
                            "params": {
                                "message": "Choose a label",
                                "requestedSchema": {
                                    "type": "object",
                                    "properties": {"label": {"type": "string", "default": None}},
                                    "required": None,
                                    "title": None,
                                },
                                "_meta": {"progressToken": 7, "private": "HOST_ONLY_META"},
                            },
                        }
                    ).encode()
                    + b"\n\n"
                )
                return httpx.Response(202)
            else:
                result = result_for(method)
            return httpx.Response(
                200,
                headers={"mcp-session-id": "fixture"} if method == "initialize" else {},
                json={"jsonrpc": "2.0", "id": message["id"], "result": result},
            )

        def factory(settings):
            client = (
                HttpMCPClient(settings, transport=httpx.MockTransport(handle))
                if transport == "http"
                else StdioMCPClient(settings)
            )
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        nested = mode == "code_mode"
        server = (
            MCPServerSettings(
                "fixture", "http", url="https://fixture.invalid", timeout_seconds=request_budget
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
                        / "elicitation_mcp_server.py"
                    ),
                    phase,
                ),
                timeout_seconds=request_budget,
            )
        )
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode="disabled" if nested else mode,
            tool_mode="code_mode" if nested else "direct",
            mcp_servers=(server,),
        )

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                assert "HOST_ONLY_META" not in repr(request)
                assert "HOST_ONLY_CONTENT" not in repr(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1 and not nested:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == (1 if nested else 2):
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
                    observation = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if expect_timeout:
                        assert "MCP tool timed out" in observation.content, observation.content
                        assert "execution outcome may be unknown" in observation.content
                        assert "Do not automatically retry" in observation.content
                    else:
                        assert "host response received" in observation.content, observation.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )

        async def host(request):
            requests.append(request)
            assert request.params["requestedSchema"] == {
                "type": "object",
                "properties": {"label": {"type": "string"}},
            }
            received.set()
            await release.wait()
            runtime.respond_mcp_elicitation(
                request.server_name,
                request.request_id,
                action,
                content={"label": "HOST_ONLY_CONTENT"},
                meta={"host": 1},
            )

        turn_started = asyncio.Event()

        async def consume():
            events = []
            async for event in runtime.stream("needle"):
                events.append(event)
                if isinstance(event, TurnStarted):
                    turn_started.set()
            return events

        task = None
        try:
            runtime.set_mcp_elicitation_handler(host)
            task = asyncio.create_task(consume())
            await asyncio.wait_for(received.wait(), 3)
            if phase == "tools/list":
                # Optional startup can await human input after Turn admission.
                await asyncio.wait_for(turn_started.wait(), 1)
                assert runtime._active_run is not None
            # Human wait still exceeds the entire admitted request budget.
            # Keep ordinary HTTP/stdio scheduling out of the tiny-budget probe below.
            await asyncio.sleep(request_budget + 0.1)
            assert not task.done()
            assert "progressToken" not in requests[0].params["_meta"]
            assert requests[0].params["_meta"]["private"] == "HOST_ONLY_META"
            with pytest.raises(ValueError):
                runtime.respond_mcp_elicitation("wrong-server", requests[0].request_id, "accept")
            release.set()
            events = await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCompleted), getattr(events[-1], "error", events[-1])
            assert "HOST_ONLY_META" not in repr(events)
            with pytest.raises(ValueError):
                runtime.respond_mcp_elicitation("fixture", requests[0].request_id, "accept")
            if transport == "http":
                assert methods.count("initialize") == 1
                if phase == "tools/call":
                    assert methods.count("tools/call") == 1
                result = {"action": action, "_meta": {"host": 1}}
                if action == "accept":
                    result["content"] = {"label": "HOST_ONLY_CONTENT"}
                assert replies == [{"jsonrpc": "2.0", "id": "server-private-id", "result": result}]
        finally:
            release.set()
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())


def test_runtime_response_delivery_after_human_wait_still_obeys_budget(tmp_path, monkeypatch):
    test_runtime_host_response_survives_human_wait(
        tmp_path,
        monkeypatch,
        "http",
        "tools/call",
        "code_mode",
        "accept",
        reply_delay=0.25,
        expect_timeout=True,
        request_budget=0.2,
    )
