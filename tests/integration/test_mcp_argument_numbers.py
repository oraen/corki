"""Discover, approve and send exact parameters through real Runtime and carriers."""

import asyncio
import base64
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_mcp_executor_http import delta, envelope, executor

from corki.cli.elicitation import collect_elicitation
from corki.config import CorkiSettings, MCPServerSettings
from corki.config.model_context import ModelContextInfo
from corki.core import LangGraphRuntime
from corki.mcp.arguments import call_arguments
from corki.mcp.client import HttpMCPClient
from corki.mcp.runtime_environment import MCPHTTPEnvironment
from corki.models import ModelCompleted
from corki.models.openai_compatible import _finish_tool_call
from corki.models.responses import _finish_function_call
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.protocol.wire_numbers import dumps_wire
from corki.tools import ToolRegistry

SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_number_server.py"
RAW = '{"n":1e999,"d":1.234567890123456789,"text":"1e999","z":-0}'
EXACT = '{"n":1e+999,"d":1.234567890123456789,"text":"1e999","z":0}'


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_discovery_approval_wire_and_reopened_history(
    tmp_path, monkeypatch, transport, mode, action
):
    async def scenario():
        reply = runpy.run_path(str(SERVER))["reply"]
        sent, reviews, notices, sampled = [], [], [], []

        def handler(request):
            if request.method == "DELETE":
                return httpx.Response(204)
            raw = request.content.decode("utf-8")
            if json.loads(raw, parse_float=str).get("method") == "tools/call":
                sent.append(raw)
                assert reviews and action == "accept"
                assert EXACT in raw
                assert int(request.headers["content-length"]) == len(request.content)
                assert request.headers["content-type"] == "application/json"
            response = reply(raw)
            return httpx.Response(202) if response is None else httpx.Response(200, json=response)

        if transport == "http":
            monkeypatch.setattr(
                "corki.mcp.manager.create_client",
                lambda settings: HttpMCPClient(settings, transport=httpx.MockTransport(handler)),
            )
        server = MCPServerSettings(
            "docs",
            transport,
            **(
                {"url": "https://numeric.test/mcp"}
                if transport == "http"
                else {"command": sys.executable, "args": ("-u", str(SERVER))}
            ),
            required=True,
            default_tools_approval_mode="prompt",
        )
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            execution_permissions=None,
            api_mode="responses" if mode == "native" else "chat_completions",
            tool_search_mode=mode,
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            mcp_servers=(server,),
            mcp_approval_policy="on-request",
        )

        class Model:
            async def stream(self, request):
                sampled.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                found = any(i.tool_name == "tool_search" for i in results)
                current = [i for i in results if i.turn_id == turn and i.tool_name != "tool_search"]
                if not found:
                    assert not any(t.name.startswith("mcp__docs") for t in request.tools)
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "numeric wire echo"}
                    )
                elif not current:
                    # Use the actual adapter argument finalizer, including its
                    # unrepresentable decoded cache, before durable model commit.
                    identity = new_tool_call_id()
                    buffer = SimpleNamespace(
                        id=identity, call_id=identity, name="mcp__docs::read", arguments=RAW
                    )
                    finish = _finish_function_call if mode == "native" else _finish_tool_call
                    call = finish(buffer)
                else:
                    assert len(current) == 1
                    assert current[0].is_error == (action != "accept")
                    if action == "accept":
                        assert EXACT in current[0].content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        identity = None
        for run in range(2):
            runtime = await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "numbers.db",
                home_path=tmp_path / "home",
                thread_id=identity,
            )

            async def host(request, runtime=runtime):
                reviews.append(request)
                assert dumps_wire(request.params["_meta"]["tool_params"]) == EXACT

                async def read(label):
                    return action

                decision, content = await collect_elicitation(request, read, notices.append)
                assert decision == action
                runtime.respond_mcp_elicitation(
                    request.server_name, request.request_id, decision, content=content
                )

            runtime.set_mcp_elicitation_handler(host)
            try:
                events = [event async for event in runtime.stream("numeric wire echo")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                identity = runtime.thread_id
                history = await runtime._repository.load_items(identity)
                calls = [
                    i.call
                    for i in history
                    if isinstance(i, ToolCallItem) and i.call.name == "mcp__docs::read"
                ]
                assert len(calls) == run + 1
                assert all(c.raw_arguments == RAW and c.arguments is None for c in calls)
            finally:
                await runtime.aclose()
        assert len(reviews) == 2
        assert sum("Tool arguments:\n" + EXACT in notice for notice in notices) == 2
        assert len(sampled) == 5, "reopening must retain discovery and not repeat prior calls"
        if transport == "http":
            assert len(sent) == (2 if action == "accept" else 0)

    asyncio.run(scenario())


def test_exact_arguments_survive_executor_base64_http_body():
    async def scenario():
        reply = runpy.run_path(str(SERVER))["reply"]
        calls = []

        async def handle(socket, packet):
            params = packet["params"]
            if params["method"] == "DELETE":
                await envelope(socket, packet, 204)
                return
            raw = base64.b64decode(params["bodyBase64"]).decode("utf-8")
            message = json.loads(raw, parse_float=str)
            if message["method"] == "tools/call":
                calls.append(raw)
                assert EXACT in raw
            response = reply(raw)
            await envelope(
                socket,
                packet,
                202 if response is None else 200,
                headers=[{"name": "content-type", "value": "application/json"}],
            )
            await delta(
                socket,
                packet,
                1,
                b"" if response is None else json.dumps(response).encode("utf-8"),
                done=True,
            )

        async with executor(handle) as (transport, _):
            settings = MCPServerSettings(
                "docs", "http", url="https://numeric.test/mcp", environment_id="worker"
            )
            client = HttpMCPClient(settings, environment=MCPHTTPEnvironment("worker", transport))
            try:
                await client.start()
                call = ToolCall(new_tool_call_id(), "mcp__docs::read", None, raw_arguments=RAW)
                result = await client.call_tool("read", call_arguments(call))
                assert EXACT in result["content"][0]["text"]
                assert len(calls) == 1 and not transport.is_closed
            finally:
                await client.aclose()
            assert not transport.is_closed, "logical MCP client only borrows executor transport"
        assert transport.is_closed

    asyncio.run(scenario())
