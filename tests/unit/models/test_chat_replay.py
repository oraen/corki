"""Provider-owned data must survive storage and tool loops without crossing endpoints."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.context.tokens import estimate_item_tokens, estimate_text_tokens
from corki.models import ModelCompleted, ModelRequest, OpenAICompatibleModel, resolve_capabilities
from corki.models.base import ModelError
from corki.protocol.chat_replay import SIGNATURE_LIMIT, replay_payload
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
    item_kind,
    item_to_payload,
)


def packets(signature="opaque-provider-signature"):
    return [
        {"choices": [{"delta": {"reasoning_content": "Use the weather tool."}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "weather", "arguments": '{"city":'},
                                "extra_content": {
                                    "google": {"thought_signature": signature, "ignored": "data"}
                                },
                            },
                            {
                                "index": 1,
                                "id": "call-2",
                                "function": {"name": "weather", "arguments": '{"city":"London"}'},
                            },
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '"Paris"}'}},
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    ]


def make_model(client, base="https://provider.test/v1"):
    return OpenAICompatibleModel(
        api_key="fixture",
        base_url=base,
        client=client,
        max_retries=0,
        capabilities=resolve_capabilities(base_url=base, api_mode="chat_completions"),
    )


def request():
    return ModelRequest(
        model="reasoning-model",
        instructions="help",
        context_items=(),
        items=(UserMessageItem("weather?", new_turn_id()),),
        tools=(),
    )


def stream_body(values):
    return "\n\n".join(f"data: {json.dumps(p)}" for p in values) + "\n\ndata: [DONE]\n\n"


def test_stream_storage_and_parallel_tool_replay_are_scoped():
    async def run():
        captured = []

        def handler(req):
            captured.append(json.loads(req.content))
            return httpx.Response(200, text=stream_body(packets()))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = make_model(client)
            req = request()
            events = [e async for e in adapter.stream(req)]
            completed = next(e for e in events if isinstance(e, ModelCompleted))
            restored = tuple(
                item_from_payload(item_kind(i), item_to_payload(i)) for i in completed.items
            )
            assert restored == completed.items
            calls = [i for i in restored if isinstance(i, ToolCallItem)]
            assert len(calls) == 2
            assert calls[1].chat_replay_json is None
            assert estimate_item_tokens(calls[0]) - estimate_item_tokens(
                replace(calls[0], chat_replay_json=None)
            ) == estimate_text_tokens("opaque-provider-signature")
            results = tuple(
                ToolResultItem(c.call.id, c.call.name, "sunny", c.turn_id) for c in calls
            )
            followup = replace(req, items=req.items + restored + results)
            # Exercise the next actual HTTP request, not just the serialization helper.
            _ = [e async for e in adapter.stream(followup)]
            messages = captured[-1]["messages"]
            assistant = next(m for m in messages if m["role"] == "assistant")
            assert assistant["reasoning_content"] == "Use the weather tool."
            assert assistant["tool_calls"][0]["extra_content"] == {
                "google": {"thought_signature": "opaque-provider-signature"}
            }
            assert "extra_content" not in assistant["tool_calls"][1]
            assert [m["role"] for m in messages[-3:]] == ["assistant", "tool", "tool"]
            for other, changed in (
                (adapter, replace(followup, model="other-model")),
                (make_model(client, "https://other.test/v1"), followup),
            ):
                message = next(
                    m for m in other._build_payload(changed)["messages"] if m["role"] == "assistant"
                )
                assert "reasoning_content" not in message
                assert all("extra_content" not in c for c in message["tool_calls"])

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad", [123, "", "x" * (SIGNATURE_LIMIT + 1)], ids=["number", "empty", "oversized"]
)
def test_invalid_signatures_are_protocol_errors(bad):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text=stream_body(packets(bad)))
            )
        ) as client:
            with pytest.raises(ModelError, match="invalid thought signature"):
                _ = [e async for e in make_model(client).stream(request())]

    asyncio.run(run())


def test_changed_signature_is_rejected_and_identical_repeat_is_accepted():
    async def run():
        for signature in ("opaque-provider-signature", "changed"):
            values = packets()
            values[2]["choices"][0]["delta"]["tool_calls"][0]["extra_content"] = {
                "google": {"thought_signature": signature}
            }
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _, rows=values: httpx.Response(200, text=stream_body(rows))
                )
            ) as client:
                if signature == "changed":
                    with pytest.raises(ModelError, match="changed a streaming thought signature"):
                        _ = [e async for e in make_model(client).stream(request())]
                else:
                    assert any(
                        isinstance(e, ModelCompleted)
                        for e in [e async for e in make_model(client).stream(request())]
                    )

    asyncio.run(run())


def test_legacy_items_and_invalid_storage_envelopes():
    from corki.protocol.items import new_step_id

    old = ReasoningItem("old reasoning", new_turn_id(), new_step_id())
    assert "chat_replay_json" not in item_to_payload(old)
    assert item_from_payload("reasoning", item_to_payload(old)) == old
    for raw in ("{}", "[]", '{"scope":"bad"}', json.dumps({"scope": "a" * 64, "untrusted": True})):
        with pytest.raises(ValueError):
            replay_payload(raw)
