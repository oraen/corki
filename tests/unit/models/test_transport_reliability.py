import asyncio
import json

import httpx
import pytest

from corki.models import (
    ModelCompleted,
    ModelError,
    ModelErrorKind,
    ModelRequest,
    ModelRetrying,
    ModelTextDelta,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.ids import ToolCallId, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ImageAttachment, ToolCall


def request() -> ModelRequest:
    return ModelRequest("test", "system", (), (UserMessageItem("hello", new_turn_id()),), ())


def chat_model(
    handler: httpx.MockTransport,
    *,
    retries: int = 0,
) -> tuple[OpenAICompatibleModel, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=handler)
    return (
        OpenAICompatibleModel(
            api_key="test",
            base_url="https://example.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://example.test/v1", api_mode="chat_completions"
            ),
            max_retries=retries,
            retry_base_seconds=0.001,
            client=client,
        ),
        client,
    )


class TerminalThenHang(httpx.AsyncByteStream):
    """Keep the connection open after a complete SSE event."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.closed = False

    async def __aiter__(self):
        yield self._body
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
def test_closing_partial_model_iterator_closes_underlying_http_response(api_mode: str) -> None:
    async def scenario() -> None:
        packet = (
            {"choices": [{"delta": {"content": "partial"}}]}
            if api_mode == "chat_completions"
            else {"type": "response.output_text.delta", "delta": "partial"}
        )
        body = TerminalThenHang(f"data: {json.dumps(packet)}\n\n".encode())
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body))
        )
        adapter = OpenAICompatibleModel if api_mode == "chat_completions" else OpenAIResponsesModel
        model = adapter(
            api_key="test",
            base_url="https://example.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://example.test/v1", api_mode=api_mode
            ),
            client=client,
        )
        stream = model.stream(request())
        try:
            assert isinstance(await anext(stream), ModelTextDelta)
            assert not body.closed
            await stream.aclose()
            assert body.closed, "HTTP response survived explicit model stream closure"
        finally:
            await stream.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_chat_returns_at_done_marker_without_waiting_for_peer_close() -> None:
    body = (
        b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    )
    stream = TerminalThenHang(body)

    async def scenario() -> list[object]:
        model, client = chat_model(
            httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
        )
        events = await asyncio.wait_for(_collect(model.stream(request())), timeout=0.5)
        await client.aclose()
        return events

    events = asyncio.run(scenario())
    assert any(isinstance(event, ModelCompleted) for event in events)
    assert stream.closed


def test_responses_returns_at_completed_event_without_waiting_for_peer_close() -> None:
    stream = TerminalThenHang(b'data: {"type":"response.completed","response":{"id":"done"}}\n\n')

    async def scenario() -> list[object]:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
        )
        model = OpenAIResponsesModel(
            api_key="test",
            base_url="https://example.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://example.test/v1", api_mode="responses"
            ),
            client=client,
        )
        events = await asyncio.wait_for(_collect(model.stream(request())), timeout=0.5)
        await client.aclose()
        return events

    events = asyncio.run(scenario())
    assert any(isinstance(event, ModelCompleted) for event in events)
    assert stream.closed


@pytest.mark.parametrize("response", [None, False, [], {}, {"id": None}, {"id": 1}])
def test_responses_invalid_completion_cannot_become_valid_empty_answer(response) -> None:
    async def scenario():
        packet = {"type": "response.completed", "response": response}
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")
            )
        )
        model = OpenAIResponsesModel(
            api_key="test",
            base_url="https://fixture.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://fixture.test/v1", api_mode="responses"
            ),
            client=client,
            max_retries=0,
        )
        try:
            with pytest.raises(ModelError) as raised:
                await _collect(model.stream(request()))
            assert raised.value.kind is ModelErrorKind.PROTOCOL
            assert not raised.value.retryable
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_responses_maps_structured_output_to_text_format() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    model = OpenAIResponsesModel(
        api_key="test",
        base_url="https://example.test/v1",
        capabilities=resolve_capabilities(base_url="https://example.test/v1", api_mode="responses"),
        client=client,
    )
    structured = ModelRequest(
        "test-model",
        "system",
        (),
        (UserMessageItem("hello", new_turn_id()),),
        (),
        output_schema=schema,
        output_schema_name="answer_contract",
    )

    payload = model._build_payload(structured)
    asyncio.run(client.aclose())

    assert payload["text"] == {
        "format": {
            "type": "json_schema",
            "name": "answer_contract",
            "strict": True,
            "schema": schema,
        }
    }


def test_non_object_stream_packet_is_a_typed_protocol_error() -> None:
    async def scenario() -> None:
        model, client = chat_model(
            httpx.MockTransport(lambda _: httpx.Response(200, text="data: []\n\ndata: [DONE]\n\n"))
        )
        with pytest.raises(ModelError) as caught:
            await _collect(model.stream(request()))
        assert caught.value.kind is ModelErrorKind.PROTOCOL
        await client.aclose()

    asyncio.run(scenario())


def test_invalid_provider_usage_is_a_typed_protocol_error() -> None:
    body = 'data: {"choices":[],"usage":{"prompt_tokens":-1}}\n\ndata: [DONE]\n\n'

    async def scenario() -> None:
        model, client = chat_model(httpx.MockTransport(lambda _: httpx.Response(200, text=body)))
        with pytest.raises(ModelError) as caught:
            await _collect(model.stream(request()))
        assert caught.value.kind is ModelErrorKind.PROTOCOL
        await client.aclose()

    asyncio.run(scenario())


def test_responses_completed_fallback_obeys_hard_response_limit() -> None:
    packet = {
        "type": "response.completed",
        "response": {
            "id": "bounded-completion",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "too long"}],
                }
            ],
        },
    }

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")
            )
        )
        model = OpenAIResponsesModel(
            api_key="test",
            base_url="https://example.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://example.test/v1", api_mode="responses"
            ),
            response_char_limit=3,
            client=client,
        )
        with pytest.raises(ModelError) as caught:
            await _collect(model.stream(request()))
        assert caught.value.kind is ModelErrorKind.PROTOCOL
        await client.aclose()

    asyncio.run(scenario())


async def _collect(stream) -> list[object]:
    return [event async for event in stream]


def test_incomplete_sse_stream_is_rejected() -> None:
    async def scenario() -> None:
        model, client = chat_model(
            httpx.MockTransport(
                lambda _: httpx.Response(
                    200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                )
            )
        )
        with pytest.raises(ModelError) as caught:
            _ = [event async for event in model.stream(request())]
        assert caught.value.kind is ModelErrorKind.PROTOCOL
        await client.aclose()

    asyncio.run(scenario())


def test_transient_failure_retries_before_any_visible_delta() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"ok"},'
                '"finish_reason":"stop"}],"usage":{"prompt_tokens":3,'
                '"completion_tokens":2}}\n\ndata: [DONE]\n\n'
            ),
        )

    async def scenario() -> list[object]:
        model, client = chat_model(httpx.MockTransport(handler), retries=2)
        events = [event async for event in model.stream(request())]
        await client.aclose()
        return events

    events = asyncio.run(scenario())
    assert attempts == 2
    assert isinstance(events[0], ModelRetrying)
    completed = next(event for event in events if isinstance(event, ModelCompleted))
    assert completed.usage.input_tokens == 3
    assert completed.usage.output_tokens == 2


def test_failure_after_visible_delta_is_never_retried() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    async def scenario() -> None:
        model, client = chat_model(httpx.MockTransport(handler), retries=3)
        with pytest.raises(ModelError):
            _ = [event async for event in model.stream(request())]
        await client.aclose()

    asyncio.run(scenario())
    assert attempts == 1


def test_responses_adapter_normalizes_text_tool_and_usage() -> None:
    packets = [
        {"type": "response.output_text.delta", "delta": "done"},
        {
            "type": "response.output_item.done",
            "item": {
                "id": "item-1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "exec_command",
                "arguments": '{"cmd":"pwd"}',
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp-1",
                "model": "gpt-test",
                "usage": {
                    "input_tokens": 7,
                    "output_tokens": 4,
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            },
        },
    ]
    body = "\n\n".join(f"data: {json.dumps(packet)}" for packet in packets)

    async def scenario() -> list[object]:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
        )
        model = OpenAIResponsesModel(
            api_key="test",
            base_url="https://example.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://example.test/v1", api_mode="responses"
            ),
            client=client,
        )
        events = [event async for event in model.stream(request())]
        await client.aclose()
        return events

    completed = next(
        event for event in asyncio.run(scenario()) if isinstance(event, ModelCompleted)
    )
    assert any(isinstance(item, AssistantMessageItem) for item in completed.items)
    assert any(isinstance(item, ToolCallItem) for item in completed.items)
    assert completed.usage.reasoning_tokens == 1
    assert completed.provider_metadata["response_id"] == "resp-1"


def test_responses_reasoning_opaque_state_is_replayed_for_tool_continuation() -> None:
    packets = [
        {
            "type": "response.output_item.done",
            "item": {
                "id": "reasoning-1",
                "type": "reasoning",
                "encrypted_content": "opaque-state",
                "summary": [{"type": "summary_text", "text": "checked files"}],
            },
        },
        {"type": "response.completed", "response": {"id": "response-1"}},
    ]
    body = "\n\n".join(f"data: {json.dumps(packet)}" for packet in packets)
    payloads: list[dict[str, object]] = []

    def handler(raw_request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(raw_request.content))
        return httpx.Response(200, text=body)

    async def scenario() -> ReasoningItem:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = OpenAIResponsesModel(
            api_key="test",
            base_url="https://example.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://example.test/v1", api_mode="responses"
            ),
            reasoning_effort="high",
            client=client,
        )
        first = next(
            event
            for event in [event async for event in model.stream(request())]
            if isinstance(event, ModelCompleted)
        )
        reasoning = next(item for item in first.items if isinstance(item, ReasoningItem))
        continued = ModelRequest(
            "test",
            "system",
            (),
            (reasoning, UserMessageItem("continue", reasoning.turn_id)),
            (),
        )
        _ = [event async for event in model.stream(continued)]
        await client.aclose()
        return reasoning

    reasoning = asyncio.run(scenario())
    assert reasoning.encrypted_content == "opaque-state"
    assert reasoning.provider_item_id == "reasoning-1"
    assert payloads[0]["include"] == ["reasoning.encrypted_content"]
    replayed = payloads[1]["input"]
    assert isinstance(replayed, list)
    assert replayed[0]["type"] == "reasoning"
    assert replayed[0]["encrypted_content"] == "opaque-state"


def test_responses_replays_tool_image_attachments_with_detail() -> None:
    captured: dict[str, object] = {}

    def handler(raw_request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(raw_request.content))
        return httpx.Response(
            200,
            text='data: {"type":"response.completed","response":{"id":"ok"}}\n\n',
        )

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = OpenAIResponsesModel(
            api_key="test",
            base_url="https://example.test/v1",
            capabilities=resolve_capabilities(
                base_url="https://example.test/v1", api_mode="responses"
            ),
            client=client,
        )
        turn_id = new_turn_id()
        call_id = ToolCallId("image-call")
        request_with_image = ModelRequest(
            "test",
            "system",
            (),
            (
                UserMessageItem("inspect", turn_id),
                ToolCallItem(
                    ToolCall(call_id, "view_image", {"path": "image.png"}),
                    turn_id,
                    new_step_id(),
                ),
                ToolResultItem(
                    call_id,
                    "view_image",
                    "loaded image",
                    turn_id,
                    attachments=(ImageAttachment("data:image/png;base64,aGVsbG8=", "original"),),
                ),
            ),
            (),
        )
        await _collect(model.stream(request_with_image))
        await client.aclose()

    asyncio.run(scenario())

    inputs = captured["input"]
    assert isinstance(inputs, list)
    output = inputs[-1]["output"]
    assert output[1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,aGVsbG8=",
        "detail": "original",
    }
