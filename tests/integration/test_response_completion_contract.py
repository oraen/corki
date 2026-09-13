import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TokenUsageUpdated, TurnCompleted, TurnFailed
from corki.protocol.items import CompactionItem, ToolCallItem, ToolResultItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.protocol.wire_numbers import WireNumber, dumps_wire
from corki.tools import ToolRegistry


def terminal(**fields):
    return {"type": "response.completed", "response": {"id": "response", **fields}}


def summary_done():
    return {
        "type": "response.output_item.done",
        "item": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "ORDINARY_SUMMARY"}],
        },
    }


USAGE = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
BAD = [
    {"usage": {}},
    {"usage": False},
    {"usage": []},
    {"usage": {"input_tokens": 1, "output_tokens": 2}},
    *(
        {"usage": {**USAGE, field: value}}
        for field in USAGE
        for value in (None, False, "1", 1.0, 2**63)
    ),
    {"usage": {**USAGE, "input_tokens_details": {}}},
    {"usage": {**USAGE, "input_tokens_details": False}},
    {"usage": {**USAGE, "input_tokens_details": {"cached_tokens": 1, "cache_write_tokens": None}}},
    {"usage": {**USAGE, "output_tokens_details": {}}},
    {"end_turn": 3},
]


def create(tmp_path, *, thread=None, registry=None):
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            tmp_path,
            api_mode="responses",
            provider_name="openai",
            api_key="fixture",
            api_base="https://fixture.invalid/v1",
            skills_enabled=False,
            model_max_retries=0,
            model_request_max_retries=0,
            model_retry_base_seconds=0.001,
        ),
        database_path=tmp_path / "s.db",
        registry=registry if registry is not None else ToolRegistry(),
        thread_id=thread,
    )


def install(monkeypatch, respond):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        http_client,
        "OwnedHTTPClient",
        lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
    )


def packet(events):
    return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))


@pytest.mark.parametrize("bad", BAD)
@pytest.mark.parametrize("later_valid", [False, True])
@pytest.mark.parametrize("compact", [False, True])
def test_invalid_completion_cannot_end_sampling_or_install_compaction(
    tmp_path, monkeypatch, bad, later_valid, compact
):
    async def scenario():
        requests = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(json.loads(request.content))
            events = [summary_done()] if compact else []
            events.append(terminal(**bad))
            if later_valid:
                events.append(terminal(usage=USAGE))
            return packet(events)

        install(monkeypatch, respond)
        runtime = create(tmp_path)
        try:
            events = (
                [e async for e in runtime.compact()]
                if compact
                else [e async for e in runtime.stream("test")]
            )
            assert isinstance(events[-1], TurnCompleted if later_valid else TurnFailed), events[-1]
            assert len(requests) == 1  # This fixture explicitly disables stream retries.
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert any(isinstance(i, CompactionItem) for i in stored) is (compact and later_valid)
            if not compact:
                saved = await runtime._repository.load_model_step(
                    runtime.thread_id, events[-1].turn_id, 0
                )
                assert (saved is not None) is later_valid
                if saved is not None:
                    assert saved.usage.total_tokens == 12
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = create(tmp_path, thread=thread)
        try:
            stored = await cold._repository.load_items(thread)
            assert any(isinstance(i, CompactionItem) for i in stored) is (compact and later_valid)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("later_valid", [False, True])
def test_done_tool_before_bad_terminal_is_not_replayed_on_cold_next_turn(
    tmp_path, monkeypatch, later_valid
):
    async def scenario():
        requests, executions = [], []
        tool = {
            "type": "function_call",
            "id": "item",
            "call_id": "call",
            "name": "guard",
            "arguments": "{}",
        }

        def respond(request):
            requests.append(json.loads(request.content))
            events = [terminal()]
            if len(requests) == 1:
                events = [{"type": "response.output_item.done", "item": tool}, terminal(usage={})]
                if later_valid:
                    events.append(terminal(usage=USAGE))
            return packet(events)

        install(monkeypatch, respond)

        class Guard:
            spec = ToolSpec("guard", "guard", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "EXECUTED_ONCE")

        def registry():
            result = ToolRegistry()
            result.register(Guard())
            return result

        runtime = create(tmp_path, registry=registry())
        try:
            events = [e async for e in runtime.stream("test")]
            assert isinstance(events[-1], TurnCompleted if later_valid else TurnFailed)
            assert executions == ["call"]
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = create(tmp_path, registry=registry(), thread=thread)
        try:
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert executions == ["call"] and len(requests) == (3 if later_valid else 2)
            stored = await cold._repository.load_items(thread)
            assert len([i for i in stored if isinstance(i, ToolCallItem)]) == 1
            assert len([i for i in stored if isinstance(i, ToolResultItem)]) == 1
            assert sum(i.get("output") == "EXECUTED_ONCE" for i in requests[-1]["input"]) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", ["failed", "incomplete", "invalid_completion"])
def test_compaction_later_valid_terminal_wins_over_pending_error(tmp_path, monkeypatch, prefix):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            bad = (
                terminal(end_turn=3)
                if prefix == "invalid_completion"
                else {
                    "type": "response." + prefix,
                    "response": {"error": {"code": "invalid_prompt"}},
                }
            )
            return packet(
                [
                    bad,
                    summary_done(),
                    terminal(),
                ]
            )

        install(monkeypatch, respond)
        runtime = create(tmp_path)
        try:
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert len(requests) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("budget", [2.5, WireNumber("1.234567890123456789"), WireNumber("1e+999")])
def test_usage_fields_metadata_and_exact_numbers_survive_runtime_and_cold_storage(
    tmp_path, monkeypatch, signed, budget
):
    async def scenario():
        usage = {
            "input_tokens": -10 if signed else 100,
            "output_tokens": -2 if signed else 10,
            "total_tokens": -12 if signed else 110,
            "input_tokens_details": {
                "cached_tokens": -4 if signed else 40,
                "cache_write_tokens": -6 if signed else 60,
            },
            "output_tokens_details": {"reasoning_tokens": -1 if signed else 5},
            "codex_rollout_budget_units": budget,
            "unknown": {"precise": WireNumber("0.12345678901234567890")},
        }
        count = 0

        def respond(request):
            nonlocal count
            count += 1
            body = terminal(
                usage=usage,
                usage_metadata={
                    "amount": "0.001",
                    "metadata": {"ignored": True},
                    "unknown": "discard",
                },
            )
            return httpx.Response(200, text="data: " + dumps_wire(body) + "\n\n")

        install(monkeypatch, respond)
        runtime = create(tmp_path)
        try:
            events = [e async for e in runtime.stream("test")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            updates = [e for e in events if isinstance(e, TokenUsageUpdated)]
            assert len(updates) == 1
            assert (
                updates[0].cache_write_tokens == usage["input_tokens_details"]["cache_write_tokens"]
            )
            assert updates[0].codex_rollout_budget_units is None
            thread, turn = runtime.thread_id, events[-1].turn_id
            saved = await runtime._repository.load_model_step(thread, turn, 0)
            assert saved.usage.total_tokens == usage["total_tokens"]
            assert saved.usage.cache_write_tokens == updates[0].cache_write_tokens
            assert saved.usage.codex_rollout_budget_units is None
            assert "usage_metadata" not in saved.provider_metadata
            await runtime._repository.commit_model_step(thread, turn, 0, saved)
        finally:
            await runtime.aclose()
        cold = create(tmp_path, thread=thread)
        try:
            recovered = await cold._repository.load_model_step(thread, turn, 0)
            assert recovered == saved and count == 1
            await cold._repository.commit_model_step(thread, turn, 0, recovered)
            measured = await cold._repository.load_context_usage(thread)
            assert measured.total_tokens == usage["total_tokens"]
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_ordinary_compaction_does_not_store_or_replay_private_usage(tmp_path, monkeypatch):
    async def scenario():
        requests = []
        usage = {
            **USAGE,
            "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 4},
            "codex_rollout_budget_units": WireNumber("1.234567890123456789"),
        }

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(json.loads(request.content))
            events = [terminal()]
            if len(requests) == 1:
                events = [
                    summary_done(),
                    terminal(usage=usage, usage_metadata={"amount": "PRIVATE_COST"}),
                ]
            return httpx.Response(
                200, text="".join("data: " + dumps_wire(e) + "\n\n" for e in events)
            )

        install(monkeypatch, respond)
        runtime = create(tmp_path)
        try:
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            thread = runtime.thread_id
            stored = await runtime._repository.load_items(thread)
            marker = next(i for i in stored if isinstance(i, CompactionItem))
            assert marker.response_metadata_json is None
            assert marker.summary == "ORDINARY_SUMMARY" and marker.remote_payload_json is None
        finally:
            await runtime.aclose()
        cold = create(tmp_path, thread=thread)
        try:
            stored = await cold._repository.load_items(thread)
            assert next(i for i in stored if isinstance(i, CompactionItem)) == marker
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert len(requests) == 2
            assert "ORDINARY_SUMMARY" in json.dumps(requests[-1])
            assert "PRIVATE_COST" not in json.dumps(requests[-1])
            assert "codex_rollout_budget_units" not in json.dumps(requests[-1])
        finally:
            await cold.aclose()

    asyncio.run(scenario())
