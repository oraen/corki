import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("partial_retry", [False, True])
def test_response_routing_state_survives_steps_retry_but_not_next_turn(
    tmp_path, monkeypatch, partial_retry
):
    async def scenario():
        requests, executions = [], []
        token = "private-routing-first"

        def respond(request):
            body = json.loads(request.content)
            requests.append((request.headers.get("x-codex-turn-state"), body))
            count = len(requests)
            if partial_retry and count == 1:
                return httpx.Response(
                    200,
                    headers={"x-codex-turn-state": token},
                    text=('data: {"type":"response.created","response":{"id":"partial"}}\n\n'),
                )
            call_step = count == (2 if partial_retry else 1)
            output = (
                [
                    {
                        "type": "function_call",
                        "name": "guard",
                        "arguments": "{}",
                        "call_id": "actual-call",
                    }
                ]
                if call_step
                else [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ]
            )
            event = {
                "type": "response.completed",
                "response": {"id": f"r-{count}", "output": output},
            }
            return httpx.Response(
                200,
                headers={"x-codex-turn-state": token if count == 1 else "later-state"},
                text=f"data: {json.dumps(event)}\n\n",
            )

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        registry = ToolRegistry()

        class Guard:
            spec = ToolSpec("guard", "guard", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "observation")

        registry.register(Guard())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                api_mode="responses",
                provider_name="custom",
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                skills_enabled=False,
                model_retry_base_seconds=0.001,
                model_request_max_retries=0,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
        )
        try:
            assert isinstance([e async for e in runtime.stream("use guard")][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("another turn")][-1], TurnCompleted)
            assert [header for header, _ in requests] == [None] * (4 if partial_retry else 3)
            assert executions == ["actual-call"]
            assert token not in repr(await runtime._repository.load_items(runtime.thread_id))
            assert token not in json.dumps([body for _, body in requests])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
