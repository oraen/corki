"""Custom input is rejected at every live boundary, regardless of legacy flags."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.models import ModelError, ModelRequest, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.items import ToolCallItem, ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolCall, ToolSpec


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("capable", [False, True])
@pytest.mark.parametrize("boundary", ["added", "done", "delta", "completed", "malformed"])
def test_custom_response_fails_without_publishing_calls(mode, capable, boundary):
    async def scenario():
        item = {"type": "custom_tool_call", "id": "i", "call_id": "c", "name": "raw", "input": "x"}
        if boundary == "completed":
            event = {"type": "response.completed", "response": {"id": "r", "output": [item]}}
        elif boundary == "delta":
            event = {"type": "response.custom_tool_call_input.delta", "item_id": "i", "delta": "x"}
        else:
            if boundary == "malformed":
                item["input"] = None
            event = {
                "type": "response.output_item." + ("added" if boundary == "added" else "done"),
                "item": item,
            }
        body = (
            "data: "
            + json.dumps(event)
            + '\n\ndata: {"type":"response.completed","response":{"id":"r"}}\n\n'
        )
        requests = []

        def respond(request):
            requests.append(request)
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            payload = json.loads(request.content)
            assert payload["tools"][0]["type"] == "function"
            history = payload["input"]
            call = next(i for i in history if i.get("type") == "function_call")
            assert json.loads(call["arguments"]) == {"input": "text('old')"}
            assert any(i.get("type") == "function_call_output" for i in history)
            assert not any(i.get("type", "").startswith("custom") for i in history)
            return httpx.Response(200, text=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=replace(
                    resolve_capabilities(
                        base_url="https://fixture.invalid/v1", api_mode="responses"
                    ),
                    supports_native_freeform=capable,
                ),
                max_retries=0,
            )
            request = ModelRequest(
                "fixture",
                "system",
                (),
                (
                    UserMessageItem("run", "t"),
                    ToolCallItem(
                        ToolCall(
                            "old", "raw", None, raw_arguments="text('old')", input_kind="freeform"
                        ),
                        "t",
                        "s",
                    ),
                    ToolResultItem("old", "raw", "done", "t", "s", input_kind="freeform"),
                ),
                (ToolSpec("raw", "fixture", {}, input_kind="freeform"),),
                tool_freeform_mode=mode,
            )
            events = []
            try:
                with pytest.raises(ModelError, match="native custom tool protocol") as error:
                    async for event in model.stream(request):
                        events.append(event)
                assert not error.value.retryable
                assert events == [] and len(requests) == 1
            finally:
                await model.aclose()

    asyncio.run(scenario())
