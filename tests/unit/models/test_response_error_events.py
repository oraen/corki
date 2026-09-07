"""SSE error priority, envelope decoding and structured-code classification."""

import asyncio
import json

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


def packet(kind, **fields):
    return "data: " + json.dumps({"type": kind, **fields}) + "\n\n"


async def collect(body, *, stream=None):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (
                httpx.Response(200, text=body)
                if stream is None
                else httpx.Response(200, stream=stream)
            )
        )
    )
    model = OpenAIResponsesModel(
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        client=client,
        capabilities=resolve_capabilities(
            base_url="https://fixture.invalid/v1", api_mode="responses"
        ),
        max_retries=0,
    )
    request = ModelRequest("fixture", "system", (), (UserMessageItem("run", new_turn_id()),), ())
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


@pytest.mark.parametrize(
    "prefix",
    [
        packet("response.failed", response={"error": {"code": "insufficient_quota"}}),
        packet(
            "response.incomplete", response={"incomplete_details": {"reason": "max_output_tokens"}}
        ),
        packet("response.completed", response={"id": "bad", "end_turn": 3}),
        packet("error", error={"code": "server_is_overloaded"}),
    ],
    ids=["failed", "incomplete", "invalid_completion", "unknown_event"],
)
def test_valid_completion_wins_over_pending_error_without_dropping_later_items(prefix):
    body = (
        prefix
        + packet(
            "response.output_item.done",
            item={
                "type": "message",
                "id": "m",
                "content": [{"type": "output_text", "text": "late"}],
            },
        )
        + packet("response.completed", response={"id": "done"})
    )
    events, error = asyncio.run(collect(body))
    assert error is None
    assert [event.item.content for event in events if isinstance(event, ModelItemCompleted)] == [
        "late"
    ]
    assert isinstance(events[-1], ModelCompleted)


def test_eof_reports_latest_error_after_publishing_later_complete_items():
    body = packet("response.failed", response={"error": {"code": "insufficient_quota"}})
    body += packet(
        "response.output_item.done",
        item={"type": "message", "id": "m", "content": [{"type": "output_text", "text": "late"}]},
    )
    body += packet(
        "response.failed",
        response={"error": {"code": "rate_limit_exceeded", "message": "try again in 2ms"}},
    )
    events, error = asyncio.run(collect(body))
    assert [event.item.content for event in events if isinstance(event, ModelItemCompleted)] == [
        "late"
    ]
    assert not any(isinstance(event, ModelCompleted) for event in events)
    assert (error.kind.value, error.retryable, error.retry_after_seconds) == (
        "rate_limit",
        True,
        0.002,
    )


def test_stream_io_failure_overrides_pending_typed_error():
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield packet(
                "response.failed", response={"error": {"code": "insufficient_quota"}}
            ).encode()
            raise httpx.ReadError("fixture lost stream")

    events, error = asyncio.run(collect("", stream=Stream()))
    assert events == []
    assert (error.kind.value, error.retryable) == ("transport", True)


@pytest.mark.parametrize(
    "raw",
    [
        {"code": "context_window_exceeded", "message": "context window"},
        {"code": "unknown", "message": "context window, try again in 3s"},
        {"code": "insufficient_quota", "message": 1},
        {"code": "insufficient_quota", "plan_type": []},
        {"code": "insufficient_quota", "resets_at": True},
        {"code": "insufficient_quota", "resets_at": 2**63},
        {"code": "insufficient_quota", "type": 1},
        {"code": []},
        [],
        None,
    ],
)
def test_message_does_not_guess_category_and_invalid_error_shapes_are_retryable(raw):
    _, error = asyncio.run(collect(packet("response.failed", response={"error": raw})))
    assert (error.kind.value, error.retryable, error.retry_after_seconds) == ("server", True, None)


@pytest.mark.parametrize("code", ["cyber_policy", "misalignment_policy_violation"])
@pytest.mark.parametrize("message", [None, "  ", "original explanation"])
def test_policy_messages_use_nonempty_fallback_without_automatic_retry(code, message):
    _, error = asyncio.run(
        collect(packet("response.failed", response={"error": {"code": code, "message": message}}))
    )
    assert error.retryable is False
    assert str(error).strip()
    if message == "original explanation":
        assert str(error) == message


@pytest.mark.parametrize(
    "message", ["try again in NaNs", "try again in -1s", "try again in " + "9" * 400 + "s"]
)
def test_invalid_rate_delay_is_not_scheduled(message):
    _, error = asyncio.run(
        collect(
            packet(
                "response.failed",
                response={"error": {"code": "rate_limit_exceeded", "message": message}},
            )
        )
    )
    assert (error.kind.value, error.retryable, error.retry_after_seconds) == (
        "rate_limit",
        True,
        None,
    )
