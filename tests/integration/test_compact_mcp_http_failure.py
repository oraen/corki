"""A live MCP compact hook timeout/cancel owns its response before Turn termination."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, CompactionItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("trigger", ["manual", "auto"])
@pytest.mark.parametrize("event", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("failure", ["timeout", "cancel"])
@pytest.mark.parametrize("parallel", [False, True])
def test_http_mcp_compact_hook_failure_closes_response_without_replay(
    tmp_path, monkeypatch, trigger, event, failure, parallel
):
    async def scenario():
        source = tmp_path / "config.toml"
        key = "pre_compact" if event == "PreCompact" else "post_compact"
        count = 2 if parallel else 1
        document = f'[[hooks.{event}]]\nmatcher="{trigger}"\n'
        for index in range(count):
            tool = f"review{index}"
            handler = {"type": "mcp_tool", "server": "policy", "tool": tool, "timeout": 1}
            fingerprint, _ = command_identity(handler, event_name=event, matcher=trigger)
            document += (
                f"[[hooks.{event}.hooks]]\n"
                f'type="mcp_tool"\nserver="policy"\ntool="{tool}"\ntimeout=1\n'
                f"[hooks.state.{json.dumps(f'{source}:{key}:0:{index}')}]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )
        started, cleaned = asyncio.Event(), asyncio.Event()
        calls, effects, summaries, normal, events, clients = [], [], [], [], [], []
        opened_bodies, closed_bodies = [], []

        class Body(httpx.AsyncByteStream):
            def __init__(self, tool):
                self.tool = tool

            async def __aiter__(self):
                opened_bodies.append(self.tool)
                if len(opened_bodies) == count:
                    started.set()
                yield b'{"jsonrpc":"2.0",'
                await asyncio.Event().wait()

            async def aclose(self):
                closed_bodies.append(self.tool)
                if len(closed_bodies) == count:
                    cleaned.set()

        def handle(request):
            assert request.url.host == "fixture.invalid"
            if request.method == "DELETE":
                return httpx.Response(204)
            message = json.loads(request.content)
            if "id" not in message:
                return httpx.Response(202)
            if message["method"] == "initialize":
                result = {
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                    "protocolVersion": "2025-06-18",
                }
            elif message["method"] == "tools/list":
                result = {
                    "tools": [
                        {"name": f"review{index}", "inputSchema": {"type": "object"}}
                        for index in range(count)
                    ]
                }
            else:
                assert message["method"] == "tools/call"
                assert message["params"]["_meta"]["threadId"] == str(runtime.thread_id)
                calls.append(message)
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    stream=Body(message["params"]["name"]),
                )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
            )

        def factory(settings):
            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Large:
            spec = ToolSpec("large", "large observation", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "x" * 16000)

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if getattr(request.items[-1], "content", None) == "SUMMARIZE_FOR_TEST":
                    summaries.append(request)
                    yield ModelCompleted((AssistantMessageItem("summary", turn, step),))
                else:
                    normal.append(request)
                    if len(normal) == 1:
                        yield ModelCompleted(
                            (
                                ToolCallItem(
                                    ToolCall(ToolCallId("large-call"), "large", {}), turn, step
                                ),
                            )
                        )
                    else:
                        yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Large())
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            context_window_tokens=8000,
            auto_compact_tokens=3000,
            compact_prompt="SUMMARIZE_FOR_TEST",
            mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.invalid"),),
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            registry=registry,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )

        async def consume():
            stream = runtime.compact() if trigger == "manual" else runtime.stream("CURRENT INPUT")
            async for item in stream:
                if isinstance(item, (TurnCompleted, TurnCancelled, TurnFailed)):
                    assert cleaned.is_set(), "terminal preceded HTTP response cleanup"
                events.append(item)

        consumer = None
        thread_id = runtime.thread_id
        try:
            await runtime._mcp_manager.start()
            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 3)
            if failure == "cancel":
                await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(consumer, 5)
            else:
                await asyncio.wait_for(consumer, 5)
            assert len(calls) == count
            assert cleaned.is_set()
            assert (
                set(opened_bodies)
                == set(closed_bodies)
                == {f"review{index}" for index in range(count)}
            )
            assert len(effects) == (1 if trigger == "auto" else 0)
            assert len(summaries) == (1 if event == "PostCompact" or failure == "timeout" else 0)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(item, CompactionItem) for item in stored) == (
                event == "PostCompact" or failure == "timeout"
            )
            terminal = [
                item
                for item in events
                if isinstance(item, (TurnCompleted, TurnCancelled, TurnFailed))
            ]
            assert len(terminal) == 1
            assert isinstance(terminal[0], TurnCancelled if failure == "cancel" else TurnCompleted)
            if failure == "timeout":
                hooks = [item for item in events if isinstance(item, HookCompleted)]
                assert len(hooks) == count and all(hook.run.status == "failed" for hook in hooks)
        finally:
            await runtime.cancel_active()
            if consumer is not None:
                await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()
        assert all(client.is_closed for client in clients)
        cold_registry = ToolRegistry()
        cold_registry.register(Large())
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            registry=cold_registry,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
            thread_id=thread_id,
        )
        try:
            assert [item async for item in cold.resume_pending()] == []
            assert await cold._repository.load_items(thread_id) == stored
            assert len(calls) == count
            assert len(effects) == (1 if trigger == "auto" else 0)
            assert len(summaries) == (1 if event == "PostCompact" or failure == "timeout" else 0)
        finally:
            await cold.aclose()

    asyncio.run(scenario())
