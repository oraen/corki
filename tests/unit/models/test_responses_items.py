"""Already completed calls cannot silently change later in the SSE response."""

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
from corki.protocol.items import AssistantMessageItem, ReasoningItem, UserMessageItem


@pytest.mark.parametrize("ending", ["identical", "changed_done", "changed_final", "late_delta"])
def test_completed_item_identity_reconciles_with_remaining_response(ending):
    async def scenario():
        item = {
            "type": "function_call",
            "id": "i",
            "call_id": "c",
            "name": "probe",
            "arguments": "{}",
        }
        packets = [{"type": "response.output_item.done", "item": item}]
        if ending in {"identical", "changed_done"}:
            packets.append(
                {
                    "type": "response.output_item.done",
                    "item": {
                        **item,
                        "arguments": "{}" if ending == "identical" else '{"different":true}',
                    },
                }
            )
        if ending == "late_delta":
            packets.append(
                {"type": "response.function_call_arguments.delta", "item_id": "i", "delta": " "}
            )
        packets.append(
            {
                "type": "response.completed",
                "response": {
                    "id": "r",
                    "output": [
                        {
                            **item,
                            "arguments": '{"different":true}'
                            if ending == "changed_final"
                            else "{}",
                        },
                    ],
                },
            }
        )
        body = "".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
        )
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        request = ModelRequest("fixture", "", (), (UserMessageItem("run", new_turn_id()),), ())
        try:
            if ending == "identical":
                events = [event async for event in model.stream(request)]
                partial = [event.item for event in events if isinstance(event, ModelItemCompleted)]
                assert len(partial) == 1
                assert isinstance(events[-1], ModelCompleted) and events[-1].items == tuple(partial)
            else:
                with pytest.raises(ModelError):
                    _ = [event async for event in model.stream(request)]
        finally:
            await model.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_undecodable_sse_events_are_skipped_before_a_valid_terminal():
    async def scenario():
        body = (
            "data: {broken\n\ndata: []\n\n"
            'data: {"type":"response.completed","response":{"id":"ok"}}\n\n'
        )
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
        )
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            max_retries=0,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        request = ModelRequest("fixture", "", (), (UserMessageItem("run", new_turn_id()),), ())
        try:
            events = [event async for event in model.stream(request)]
            assert len(events) == 1 and isinstance(events[0], ModelCompleted)
            assert events[0].provider_metadata["response_id"] == "ok"
        finally:
            await model.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("identity", ["anonymous", "index", "reasoning_fallback"])
def test_compatibility_deltas_do_not_duplicate_final_items_or_invent_provider_ids(identity):
    async def scenario():
        delta = {"type": "response.output_text.delta", "delta": "hello"}
        output = [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]
        if identity == "index":
            delta["output_index"] = 0
            output[0]["id"] = "message-0"
        elif identity == "reasoning_fallback":
            delta["type"] = "response.reasoning_summary_text.delta"
            output = []
        packets = [delta, {"type": "response.completed", "response": {"id": "r", "output": output}}]
        body = "".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
        )
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        request = ModelRequest("fixture", "", (), (UserMessageItem("run", new_turn_id()),), ())
        try:
            events = [event async for event in model.stream(request)]
            completed = events[-1]
            assert len(completed.items) == 1 and completed.items[0].content == "hello"
            if identity == "reasoning_fallback":
                assert isinstance(completed.items[0], ReasoningItem)
                assert completed.items[0].provider_item_id is None
            else:
                assert isinstance(completed.items[0], AssistantMessageItem)
                assert completed.items[0].id == events[0].item_id
        finally:
            await model.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["changed_text", "changed_opaque", "changed_phase", "late_delta", "combined_budget"]
)
def test_complete_content_contract_rejects_mutation_and_combined_overflow(fault):
    async def scenario():
        item = {
            "type": "message",
            "id": "m",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "x" * 30}],
        }
        if fault == "changed_opaque":
            item = {"type": "reasoning", "id": "r", "encrypted_content": "opaque", "summary": []}
        packets = [{"type": "response.output_item.done", "item": item}]
        if fault == "changed_text":
            changed = {**item, "content": [{"type": "output_text", "text": "changed"}]}
        elif fault == "changed_opaque":
            changed = {**item, "encrypted_content": "different"}
        else:
            changed = {**item, "phase": "final_answer"}
        if fault == "late_delta":
            packets.append({"type": "response.output_text.delta", "item_id": "m", "delta": "late"})
        elif fault == "combined_budget":
            packets.append(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "id": "t",
                        "call_id": "c",
                        "name": "probe",
                        "arguments": json.dumps({"value": "x" * 20}),
                    },
                }
            )
        else:
            packets.append({"type": "response.output_item.done", "item": changed})
        packets.append({"type": "response.completed", "response": {"id": "r"}})
        body = "".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
        )
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            response_char_limit=50 if fault == "combined_budget" else 1000,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        request = ModelRequest("fixture", "", (), (UserMessageItem("run", new_turn_id()),), ())
        emitted = []
        try:
            with pytest.raises(ModelError):
                async for event in model.stream(request):
                    emitted.append(event)
            assert len(emitted) == 1 and isinstance(emitted[0], ModelItemCompleted)
        finally:
            await model.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_tool_order_follows_completed_items_not_early_added_buffers():
    async def scenario():
        first = {
            "type": "function_call",
            "id": "i1",
            "call_id": "c1",
            "name": "probe",
            "arguments": "{}",
        }
        second = {**first, "id": "i2", "call_id": "c2"}
        packets = (
            [{"type": "response.output_item.added", "item": item} for item in (first, second)]
            + [{"type": "response.output_item.done", "item": item} for item in (second, first)]
            + [{"type": "response.completed", "response": {"id": "r"}}]
        )
        body = "".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
        )
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        request = ModelRequest("fixture", "", (), (UserMessageItem("run", new_turn_id()),), ())
        try:
            events = [event async for event in model.stream(request)]
            partial = tuple(event.item for event in events if isinstance(event, ModelItemCompleted))
            assert [item.call.id for item in partial] == ["c2", "c1"]
            assert events[-1].items == partial
        finally:
            await model.aclose()
            await client.aclose()

    asyncio.run(scenario())
