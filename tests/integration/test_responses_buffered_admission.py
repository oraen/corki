import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import CompactionItem, RemoteHistoryItem, ToolCallItem, UserMessageItem
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import dumps_wire
from corki.tools import ToolRegistry

NUMBER = "$serde_json::private::Number"
RAW = "$serde_json::private::RawValue"


def encoded(number, mode):
    return number if mode == "integer" else {NUMBER if mode == "number_map" else RAW: str(number)}


def response(*events):
    return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))


def done(item):
    return {"type": "response.output_item.done", "item": item}


def terminal(output=()):
    return {"type": "response.completed", "response": {"id": "complete", "output": list(output)}}


def message(text, **fields):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
        **fields,
    }


def install(monkeypatch, respond):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        http_client,
        "OwnedHTTPClient",
        lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
    )


async def create(tmp_path, *, registry=None, thread=None, legacy=False, native=False):
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            tmp_path,
            api_mode="responses",
            provider_name="openai",
            api_key="fixture",
            api_base="https://fixture.invalid/v1",
            skills_enabled=False,
            tool_search_mode="native" if native else "compatible",
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            remote_compaction_v2=not legacy,
            model_request_max_retries=0,
            model_max_retries=0,
            model_retry_base_seconds=0.001,
        ),
        database_path=tmp_path / "s.db",
        registry=registry or ToolRegistry(),
        thread_id=thread,
    )


@pytest.mark.parametrize("mode", ["integer", "number_map", "raw_map"])
@pytest.mark.parametrize(
    "case",
    [
        "done",
        "added",
        "unfinished",
        "done_after_added",
        "terminal_echo",
        "terminal_only",
        "terminal_replaces_preview",
        "later_valid",
        "later_bad",
    ],
)
def test_invalid_buffered_item_never_becomes_an_executable_call(tmp_path, monkeypatch, mode, case):
    async def scenario():
        requests, executions = [], []
        call = {
            "type": "function_call",
            "id": "item",
            "call_id": "call",
            "name": "guard",
            "arguments": '{"origin":"good"}',
        }
        bad = {
            **call,
            "arguments": '{"origin":"bad"}',
            "unknown": [{"nested": encoded(2**64, mode)}],
        }
        preview = {
            "type": "response.output_item.added",
            "item": {**call, "arguments": '{"origin":"preview"}'},
        }
        added_bad = {"type": "response.output_item.added", "item": bad}
        packets = {
            "done": [done(bad), terminal()],
            "added": [added_bad, terminal()],
            "unfinished": [preview, terminal()],
            "done_after_added": [preview, done(bad), terminal()],
            "terminal_echo": [preview, done(bad), terminal([bad])],
            "terminal_only": [terminal([bad])],
            "terminal_replaces_preview": [preview, terminal([call])],
            "later_valid": [added_bad, done(bad), done(call), terminal()],
            "later_bad": [done(call), done(bad), terminal([bad])],
        }[case]

        def respond(request):
            requests.append(request)
            return response(*(packets if len(requests) == 1 else [terminal()]))

        install(monkeypatch, respond)

        class Guard:
            spec = ToolSpec("guard", "guard", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.arguments["origin"])
                return ToolResult(call.id, call.name, "observation")

        def registry():
            result = ToolRegistry()
            result.register(Guard())
            return result

        runtime = await create(tmp_path, registry=registry())
        expected = (
            ["good"] if case in {"later_valid", "later_bad", "terminal_replaces_preview"} else []
        )
        try:
            assert isinstance(
                [e async for e in runtime.stream("test admission")][-1], TurnCompleted
            )
            assert executions == expected
            assert len(requests) == (2 if expected else 1)
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = await create(tmp_path, registry=registry(), thread=thread)
        try:
            assert isinstance([e async for e in cold.stream("continue")][-1], TurnCompleted)
            assert executions == expected
            calls = [
                i for i in await cold._repository.load_items(thread) if isinstance(i, ToolCallItem)
            ]
            assert [i.call.arguments["origin"] for i in calls] == expected
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["legacy_number", "v2_number", "v2_raw_number", "v2_raw_item"])
def test_archived_private_numbers_are_filtered_without_rewriting_archive(
    tmp_path, monkeypatch, mode
):
    async def scenario():
        item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "old"}],
            "internal_chat_message_metadata_passthrough": {
                "create_time": {RAW if mode == "v2_raw_number" else NUMBER: "1.2300"}
            },
        }
        if mode == "v2_raw_item":
            item = {RAW: json.dumps(item)}
        legacy = mode == "legacy_number"
        requests = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(request.content)
            if len(requests) > 1:
                return response(terminal())
            return response(done(message("SUMMARY")), terminal())

        install(monkeypatch, respond)
        runtime = await create(tmp_path, legacy=legacy)
        try:
            await runtime._ensure_ready()
            # Seed the normalized archive produced by the former decoder, not
            # a new native response or an undecoded serialization wrapper.
            normalized = materialize(loads_wire(json.dumps(item)), preserve_pairs=False)
            archived = RemoteHistoryItem(dumps_wire(normalized), "old")
            await runtime._repository.append_items(runtime.thread_id, (archived,))
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = await create(tmp_path, thread=thread, legacy=legacy)
        try:
            assert isinstance([e async for e in cold.stream("recall")][-1], TurnCompleted)
            assert b"internal_chat_message_metadata_passthrough" not in requests[0]
            assert b"create_time" not in requests[0]
            assert b"old" in requests[0]
            assert NUMBER.encode() not in requests[0] and RAW.encode() not in requests[0]
            assert b"SUMMARY" in requests[-1] and len(requests) == 2
            stored = await cold._repository.load_items(thread)
            assert next(i for i in stored if isinstance(i, RemoteHistoryItem)) == archived
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "tool_search_call", "execution": "client", "arguments": {NUMBER: []}},
        {
            "type": "tool_search_output",
            "execution": "server",
            "status": "completed",
            "tools": [{RAW: "invalid"}],
        },
    ],
)
def test_native_search_with_private_invalid_values_cannot_install_summary(
    tmp_path, monkeypatch, bad
):
    async def scenario():
        requests = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(request)
            return response(terminal([bad, message("MUST_NOT_INSTALL")]))

        install(monkeypatch, respond)
        runtime = await create(tmp_path, legacy=True)
        try:
            await runtime._ensure_ready()
            await runtime._repository.append_items(
                runtime.thread_id, (UserMessageItem("old", "old"),)
            )
            before = await runtime._repository.load_items(runtime.thread_id)
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnFailed), events[-1]
            # The malformed private wrapper invalidates the entire envelope
            # before native-item inspection. No valid terminal survives.
            assert "response.completed" in events[-1].error
            assert len(requests) == 1
            assert await runtime._repository.load_items(runtime.thread_id) == before
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_ordinary_search_under_legacy_native_setting_loads_and_executes_once(tmp_path, monkeypatch):
    async def scenario():
        requests, executions = [], []

        class Calendar:
            spec = ToolSpec(
                "calendar", "calendar meeting", {"type": "object"}, exposure=ToolExposure.DEFERRED
            )

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "created")

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                assert all(t.get("name") != "calendar" for t in body["tools"])
                return response(
                    done(
                        {
                            "type": "function_call",
                            "call_id": "search",
                            "name": "tool_search",
                            "arguments": json.dumps({"query": "calendar", "limit": 1}),
                        }
                    ),
                    terminal(),
                )
            assert any(tool.get("name") == "calendar" for tool in body["tools"])
            assert all(tool["type"] == "function" for tool in body["tools"])
            assert not any(i.get("type") == "tool_search_output" for i in body["input"])
            if len(requests) == 2:
                return response(
                    done(
                        {
                            "type": "function_call",
                            "call_id": "calendar-call",
                            "name": "calendar",
                            "arguments": "{}",
                        }
                    ),
                    terminal(),
                )
            assert any(
                i.get("type") == "function_call_output" and "created" in i["output"]
                for i in body["input"]
            )
            return response(terminal())

        install(monkeypatch, respond)

        def registry():
            result = ToolRegistry()
            result.register(Calendar())
            return result

        runtime = await create(tmp_path, registry=registry(), native=True)
        try:
            assert isinstance([e async for e in runtime.stream("schedule")][-1], TurnCompleted)
            assert len(requests) == 3 and executions == ["calendar-call"]
            thread = runtime.thread_id
            before = await runtime._repository.load_items(thread)
        finally:
            await runtime.aclose()
        cold = await create(tmp_path, registry=registry(), thread=thread, native=True)
        try:
            assert isinstance([e async for e in cold.stream("recall")][-1], TurnCompleted)
            assert len(requests) == 4 and executions == ["calendar-call"]
            stored = await cold._repository.load_items(thread)
            assert stored[: len(before)] == before
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("number", [2**64, 2**128 - 1, -(2**63) - 1, -(2**127)])
@pytest.mark.parametrize("mode", ["integer", "number_map", "raw_map"])
def test_ordinary_summary_discards_bad_buffered_item_before_persistence(
    tmp_path, monkeypatch, number, mode
):
    async def scenario():
        requests = []
        bad = message("BAD", unknown=encoded(number, mode))
        good = message("GOOD")

        def respond(request):
            requests.append(json.loads(request.content))
            return (
                response(done(bad), done(good), terminal())
                if len(requests) == 1
                else response(terminal())
            )

        install(monkeypatch, respond)
        runtime = await create(tmp_path)
        try:
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = await create(tmp_path, thread=thread)
        try:
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert "GOOD" in json.dumps(requests[-1]) and "BAD" not in json.dumps(requests[-1])
            stored = await cold._repository.load_items(thread)
            items = [i for i in stored if isinstance(i, CompactionItem)]
            assert len(items) == 1 and items[0].summary == "GOOD"
            assert items[0].remote_payload_json is None
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("number", [2**64, 2**128 - 1, -(2**63) - 1, -(2**127)])
def test_private_root_metadata_numbers_do_not_enter_ordinary_summary_archive(
    tmp_path, monkeypatch, number
):
    async def scenario():
        raw = message("SUMMARY", internal_chat_message_metadata_passthrough={"create_time": number})
        install(monkeypatch, lambda _: response(done(raw), terminal()))
        runtime = await create(tmp_path, legacy=True)
        try:
            await runtime._ensure_ready()
            await runtime._repository.append_items(
                runtime.thread_id, (UserMessageItem("old", "old"),)
            )
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            items = [
                i
                for i in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(i, RemoteHistoryItem)
            ]
            assert not items
            stored = await runtime._repository.load_items(runtime.thread_id)
            marker = next(i for i in stored if isinstance(i, CompactionItem))
            assert marker.summary == "SUMMARY" and marker.remote_payload_json is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_bad_item_does_not_substitute_for_missing_stream_terminal(tmp_path, monkeypatch):
    async def scenario():
        install(
            monkeypatch,
            lambda _: response(done(message("BAD", unknown=2**64))),
        )
        runtime = await create(tmp_path)
        try:
            assert isinstance([e async for e in runtime.compact()][-1], TurnFailed)
            assert not any(
                isinstance(i, CompactionItem)
                for i in await runtime._repository.load_items(runtime.thread_id)
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
