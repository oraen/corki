"""Native search responses cannot end an ordinary-function Turn successfully."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import HostedToolItem, ToolCallItem, ToolResultItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("kind", ["client", "server", "output", "web", "notification"])
@pytest.mark.parametrize("boundary", ["added", "done", "completed", "delta"])
@pytest.mark.parametrize("partial", [False, True])
def test_native_search_response_is_fatal(tmp_path, monkeypatch, provider, kind, boundary, partial):
    async def scenario():
        requests = []
        item = {
            "type": "tool_search_call",
            "id": "s",
            "call_id": "c",
            "execution": kind,
            "arguments": {"query": "needle"},
        }
        if kind == "output":
            item = {
                "type": "tool_search_output",
                "execution": "server",
                "status": "completed",
                "tools": [],
            }
        elif kind == "web":
            item = {
                "type": "web_search_call",
                "id": "w",
                "action": {"type": "search", "query": "needle"},
            }
        elif kind == "notification":
            item = {"type": "function_call_output", "output": "unsolicited", "call_id": None}
        if boundary == "completed":
            event = {"type": "response.completed", "response": {"id": "r", "output": [item]}}
        elif boundary == "delta":
            event = {
                "type": "response.web_search_call.in_progress"
                if kind == "web"
                else "response.function_call_output.delta"
                if kind == "notification"
                else "response.tool_search_call.arguments.delta",
                "item_id": "s",
                "delta": "{}",
            }
        else:
            event = {"type": "response.output_item." + boundary, "item": item}
        packets = (
            [{"type": "response.output_text.delta", "item_id": "m", "delta": "partial"}]
            if partial
            else []
        )
        packets += [event, {"type": "response.completed", "response": {"id": "r"}}]

        def respond(request):
            requests.append(request)
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            return httpx.Response(
                200, text="".join("data: " + json.dumps(p) + "\n\n" for p in packets)
            )

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client_type(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                api_mode="responses",
                api_base="https://fixture.invalid/v1",
                api_key="fixture",
                provider_name=provider,
                skills_enabled=False,
                tool_search_mode="native",
                model_max_retries=0,
                model_request_max_retries=0,
            ),
            database_path=tmp_path / "history.db",
            registry=ToolRegistry(),
        )
        try:
            events = [event async for event in runtime.stream("search")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert (
                "hosted tool protocol"
                if kind in {"web", "notification"}
                else "native tool search protocol"
            ) in events[-1].error
            assert not events[-1].retryable
            assert not any(isinstance(event, TurnCompleted) for event in events)
            assert len(requests) == 1
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert not any(
                isinstance(item, (HostedToolItem, ToolCallItem, ToolResultItem)) for item in stored
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
