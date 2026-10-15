import asyncio
import json

import httpx
import pytest

from corki.models import ModelRequest, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.items import ToolCallItem, ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolCall, ToolSpec


@pytest.mark.parametrize("mode", ["native", "compatible"])
def test_sdk_search_declaration_and_history_are_ordinary_functions(mode):
    search = ToolSpec("tool_search", "Find tools", {"type": "object"})
    request = ModelRequest(
        "fixture",
        "",
        (),
        (
            UserMessageItem("find", "turn"),
            ToolCallItem(ToolCall("call", "tool_search", {"query": "read"}), "turn", "step"),
            ToolResultItem("call", "tool_search", "found", "turn"),
        ),
        (search,),
        tool_search_mode=mode,
    )
    model = OpenAIResponsesModel(
        api_key="fixture",
        base_url="https://fixture.invalid",
        client=object(),
        capabilities=resolve_capabilities(base_url="https://fixture.invalid", api_mode="responses"),
    )
    payload = model._build_payload(request)
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["name"] == "tool_search"
    assert payload["input"][1]["type"] == "function_call"
    assert payload["input"][2]["type"] == "function_call_output"
    assert payload["input"][2]["output"] == "found"
    assert payload["input"][1]["call_id"] == payload["input"][2]["call_id"] == "call"


@pytest.mark.parametrize("done", [False, True])
def test_native_search_response_cannot_create_executable_calls(done):
    from corki.models import ModelError

    async def scenario():
        item = {
            "type": "tool_search_call",
            "id": "i",
            "call_id": "c",
            "execution": "client",
            "arguments": {"query": "read"},
        }
        packets = [{"type": "response.output_item.done", "item": item}] if done else []
        packets.append({"type": "response.completed", "response": {"id": "r", "output": [item]}})

        def respond(request):
            return httpx.Response(
                200, text="".join("data: " + json.dumps(p) + "\n\n" for p in packets)
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid",
                client=client,
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid", api_mode="responses"
                ),
            )
            request = ModelRequest("fixture", "", (), (UserMessageItem("find", "turn"),), ())
            events = []
            with pytest.raises(ModelError, match="native tool search protocol"):
                async for event in model.stream(request):
                    events.append(event)
            assert events == []

    asyncio.run(scenario())
