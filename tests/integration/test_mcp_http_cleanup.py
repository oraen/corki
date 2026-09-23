import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("failure_phase", ["tool", "recovery"])
def test_runtime_cancel_waits_for_response_cleanup_and_cold_history_does_not_replay(
    tmp_path, monkeypatch, mode, close_fails, failure_phase
):
    async def scenario():
        closing, release = asyncio.Event(), asyncio.Event()
        calls, cleaned, owners, clients, events = [], [], [], [], []

        class Stream(httpx.AsyncByteStream):
            def __init__(self, request_id, result):
                self.request_id, self.result = request_id, result

            async def __aiter__(self):
                owners.append(asyncio.current_task())
                yield json.dumps(
                    {"jsonrpc": "2.0", "id": self.request_id, "result": self.result}
                ).encode()

            async def aclose(self):
                closing.set()
                await release.wait()
                cleaned.append(True)
                if close_fails:
                    raise OSError("PRIVATE close detail")

        def factory(settings):
            initializations = 0

            def handle(request):
                nonlocal initializations
                if request.method == "DELETE":
                    return httpx.Response(204)
                message = json.loads(request.content)
                if "id" not in message:
                    return httpx.Response(202)
                if message["method"] == "initialize":
                    initializations += 1
                    result = {
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                    }
                    if failure_phase == "recovery" and initializations == 2:
                        return httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            stream=Stream(message["id"], result),
                        )
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
                    if failure_phase == "recovery":
                        return httpx.Response(404)
                    return httpx.Response(200, stream=Stream(message["id"], {"content": []}))
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                    headers={"mcp-session-id": str(initializations)},
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                else:
                    assert self.count == 2
                    call = ToolCall(new_tool_call_id(), "mcp__docs::read", {})
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode=mode,
            mcp_servers=(MCPServerSettings("docs", "http", url="https://fixture.invalid"),),
        )

        async def create(model, thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=model,
                registry=ToolRegistry(),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                thread_id=thread,
            )

        runtime = await create(Model())

        async def consume():
            try:
                async for event in runtime.stream("needle"):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(closing.wait(), 3)
            await runtime.cancel_active()
            for _ in range(3):
                owners[0].cancel()
                await asyncio.sleep(0)
            await asyncio.sleep(0.02)
            assert not consumer.done(), "Runtime published terminal before response cleanup"
            assert not any(
                isinstance(e, (TurnCancelled, TurnCompleted, TurnFailed)) for e in events
            )
            release.set()
            await asyncio.wait_for(consumer, 3)
            assert len(cleaned) == len(calls) == 1
            assert sum(isinstance(e, TurnCancelled) for e in events) == 1
            assert not any(isinstance(e, (TurnCompleted, TurnFailed)) for e in events)
            raw = await runtime._repository.load_items(runtime._thread_id)
        finally:
            release.set()
            await runtime.cancel_active()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

        class Cold:
            async def stream(self, request):
                assert len(calls) == 1
                for item in request.items:
                    if isinstance(item, ToolResultItem) and item.tool_name == "mcp__docs::read":
                        assert item.is_error and "PRIVATE" not in item.content
                yield ModelCompleted(
                    (AssistantMessageItem("cold", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        cold = await create(Cold(), runtime._thread_id)
        try:
            result = [e async for e in cold.stream("continue")]
            assert isinstance(result[-1], TurnCompleted)
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw and len(calls) == 1
        finally:
            await cold.aclose()
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())
