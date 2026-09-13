"""Legacy custom streams must fail without publishing executable items."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.models import (
    ModelCompleted,
    ModelError,
    ModelItemCompleted,
    ModelRequest,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import ToolSpec

ITEM = {
    "type": "custom_tool_call",
    "id": "i",
    "call_id": "c",
    "name": "raw",
    "input": "text('原样')",
}


def packet(kind, **fields):
    return "data: " + json.dumps({"type": kind, **fields}) + "\n\n"


async def collect(body, *, limit=10000, capable=True):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    )
    model = OpenAIResponsesModel(
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        client=client,
        capabilities=replace(
            resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
            supports_native_freeform=capable,
        ),
        max_retries=0,
        response_char_limit=limit,
    )
    request = ModelRequest(
        "fixture",
        "system",
        (),
        (UserMessageItem("run", new_turn_id()),),
        (ToolSpec("raw", "fixture", {}, input_kind="freeform"),),
        tool_freeform_mode="native",
    )
    events = []
    try:
        async for event in model.stream(request):
            events.append(event)
        return events, None
    except ModelError as error:
        return events, error
    finally:
        await model.aclose()
        await client.aclose()


@pytest.mark.parametrize("identity", [{"item_id": "i"}, {"call_id": "c"}, {"output_index": 0}])
def test_custom_aliases_and_repeated_terminal_cannot_publish_a_call(identity):
    body = packet("response.output_item.added", item={**ITEM, "input": ""}, output_index=0)
    body += packet("response.custom_tool_call_input.delta", delta=ITEM["input"], **identity)
    body += packet("response.output_item.done", item=ITEM, output_index=0)
    body += packet("response.output_item.done", item=ITEM)
    body += packet("response.completed", response={"id": "r", "output": [ITEM]})
    # Even a budget large enough for the old native item cannot authorize it.
    metadata_chars = len(json.dumps({"id": ITEM["id"]}, separators=(",", ":")))
    events, error = asyncio.run(collect(body, limit=len(ITEM["input"]) + metadata_chars))
    assert error is not None and not error.retryable
    published = [event.item for event in events if isinstance(event, ModelItemCompleted)]
    assert published == [] and events == []


@pytest.mark.parametrize(
    "suffix",
    [
        packet("response.custom_tool_call_input.delta", call_id="c", delta="mutated"),
        packet("response.output_item.done", item={**ITEM, "input": "changed"}),
        packet(
            "response.completed", response={"id": "r", "output": [{**ITEM, "input": "changed"}]}
        ),
    ],
)
def test_custom_completion_is_rejected_before_following_mutations(suffix):
    events, error = asyncio.run(collect(packet("response.output_item.done", item=ITEM) + suffix))
    assert error is not None and not error.retryable
    assert not any(isinstance(event, ModelItemCompleted) for event in events)
    assert not any(isinstance(event, ModelCompleted) for event in events)


@pytest.mark.parametrize("patch", [{"input": None}, {"input": {}}, {"call_id": None}, {"name": 3}])
def test_bad_custom_completion_never_publishes_a_call(patch):
    events, error = asyncio.run(
        collect(packet("response.output_item.done", item={**ITEM, **patch}))
    )
    assert events == [] and error is not None


@pytest.mark.parametrize("terminal", [False, True])
def test_partial_input_never_becomes_an_executable_call(terminal):
    body = packet("response.output_item.added", item={**ITEM, "input": ""})
    body += packet("response.custom_tool_call_input.delta", call_id="c", delta=ITEM["input"])
    if terminal:
        body += packet("response.completed", response={"id": "r"})
    events, error = asyncio.run(collect(body))
    assert not any(isinstance(event, ModelItemCompleted) for event in events)
    assert error is not None and not error.retryable
    assert events == []


def test_custom_delta_after_text_is_fatal_not_a_successful_partial_response():
    body = packet("response.output_text.delta", item_id="m", delta="other")
    body += packet("response.custom_tool_call_input.delta", call_id="c", delta="x" * 10)
    events, error = asyncio.run(collect(body, limit=12))
    assert error is not None and not error.retryable
    assert "native custom tool protocol" in str(error)
    assert len(events) == 1 and events[0].delta == "other"
    assert not any(isinstance(event, ModelItemCompleted) for event in events)
    assert not any(isinstance(event, ModelCompleted) for event in events)


@pytest.mark.parametrize("capable", [False, True])
def test_capability_cannot_enable_custom_protocol(capable):
    events, error = asyncio.run(
        collect(packet("response.output_item.done", item=ITEM), capable=capable)
    )
    assert events == [] and "native custom tool protocol" in str(error)
