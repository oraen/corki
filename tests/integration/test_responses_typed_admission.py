import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import ToolCallItem, ToolResultItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry

META = "internal_chat_message_metadata_passthrough"
MISSING = object()


def done(item):
    return {"type": "response.output_item.done", "item": item}


def terminal(output=()):
    return {"type": "response.completed", "response": {"id": "terminal", "output": list(output)}}


def install(monkeypatch, respond):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        http_client,
        "OwnedHTTPClient",
        lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
    )


def response(events):
    return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))


def create(tmp_path, registry, *, thread=None, custom=False):
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            tmp_path,
            api_mode="responses",
            provider_name="openai",
            api_key="fixture",
            api_base="https://fixture.invalid/v1",
            skills_enabled=False,
            tool_freeform_mode="native" if custom else "compatible",
            model_max_retries=0,
            model_request_max_retries=0,
        ),
        database_path=tmp_path / "s.db",
        registry=registry,
        thread_id=thread,
    )


BAD_FIELDS = [
    ("function_call", "name", None),
    ("function_call", "name", 3),
    ("function_call", "name", MISSING),
    ("function_call", "arguments", None),
    ("function_call", "arguments", {}),
    ("function_call", "arguments", MISSING),
    ("function_call", "call_id", None),
    ("function_call", "call_id", 3),
    ("function_call", "call_id", MISSING),
    ("function_call", "namespace", []),
    ("function_call", "id", 3),
    ("function_call", "encrypted_function_args", [1]),
    ("function_call", META, {"turn_id": []}),
    ("function_call", META, {"create_time": "bad"}),
    ("custom_tool_call", "name", 3),
    ("custom_tool_call", "input", None),
    ("custom_tool_call", "namespace", {}),
    ("custom_tool_call", "call_id", None),
]


@pytest.mark.parametrize("boundary", ["done", "added", "terminal"])
@pytest.mark.parametrize("kind,field,value", BAD_FIELDS)
def test_bad_tool_shape_cannot_use_a_preview_or_terminal_echo(
    tmp_path, monkeypatch, boundary, kind, field, value
):
    async def scenario():
        requests, executions = [], []
        custom = kind == "custom_tool_call"
        call = {
            "type": kind,
            "id": "same",
            "call_id": "call",
            "name": "guard",
            "input" if custom else "arguments": "raw" if custom else "{}",
        }
        bad = {**call, field: value}
        if value is MISSING:
            bad.pop(field)
        packets = (
            [{"type": "response.output_item.added", "item": bad}, terminal()]
            if boundary == "added"
            else [terminal([bad])]
            if boundary == "terminal"
            else [{"type": "response.output_item.added", "item": call}, done(bad), terminal([bad])]
        )

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(request)
            return response(packets if len(requests) == 1 else [terminal()])

        install(monkeypatch, respond)

        class Guard:
            spec = ToolSpec(
                "guard", "guard", {"type": "object"}, input_kind="freeform" if custom else "json"
            )

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "MUST_NOT_EXECUTE")

        def registry():
            result = ToolRegistry()
            result.register(Guard())
            return result

        runtime = create(tmp_path, registry(), custom=custom)
        rejected = custom or field == "namespace"
        accepted = field in {META, "encrypted_function_args"} and boundary != "added"
        expected = ["call"] if accepted else []
        try:
            events = [e async for e in runtime.stream("test")]
            assert isinstance(events[-1], TurnFailed if rejected else TurnCompleted), events[-1]
            if rejected:
                assert not events[-1].retryable
            assert executions == expected and len(requests) == (2 if accepted else 1)
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = create(tmp_path, registry(), custom=custom, thread=thread)
        try:
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert executions == expected and len(requests) == (3 if accepted else 2)
            stored = await cold._repository.load_items(thread)
            assert sum(isinstance(i, ToolCallItem) for i in stored) == int(accepted)
            assert sum(isinstance(i, ToolResultItem) for i in stored) == int(accepted)
            for request in requests[1:]:
                for item in json.loads(request.content)["input"]:
                    assert META not in item and "encrypted_function_args" not in item
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["done", "terminal"])
@pytest.mark.parametrize(
    "case", ["valid_after_invalid", "empty_name", "whitespace_name", "empty_call_id"]
)
@pytest.mark.parametrize("custom", [False, True])
def test_complete_required_strings_replace_preview_without_poisoning_or_replay(
    tmp_path, monkeypatch, boundary, case, custom
):
    async def scenario():
        requests, executions = [], []
        preview = {
            "type": "function_call",
            "id": "same",
            "call_id": "preview-call",
            "name": "guard",
            "arguments": json.dumps({"input": '{"source":"preview"}'})
            if custom
            else '{"source":"preview"}',
        }
        completed = {
            **preview,
            "call_id": "" if case == "empty_call_id" else "completed-call",
            "name": "" if case == "empty_name" else " \t" if case == "whitespace_name" else "guard",
            "arguments": json.dumps({"input": '{"source":"complete"}'})
            if custom
            else '{"source":"complete"}',
        }
        packets = [
            {"type": "response.output_item.added", "item": preview},
            done({**completed, "name": None}),
        ]
        packets += (
            [done(completed), done(completed), terminal([completed])]
            if boundary == "done"
            else [terminal([completed])]
        )

        def respond(request):
            requests.append(json.loads(request.content))
            return response(packets if len(requests) == 1 else [terminal()])

        install(monkeypatch, respond)

        class Guard:
            spec = ToolSpec(
                "guard", "guard", {"type": "object"}, input_kind="freeform" if custom else "json"
            )

            async def execute(self, call, context):
                executions.append((call.id, call.raw_arguments if custom else call.arguments))
                return ToolResult(call.id, call.name, "GUARD_EXECUTED")

        def registry():
            result = ToolRegistry()
            result.register(Guard())
            return result

        expected = (
            []
            if case in {"empty_name", "whitespace_name"}
            else [
                (
                    completed["call_id"],
                    '{"source":"complete"}' if custom else {"source": "complete"},
                )
            ]
        )
        runtime = create(tmp_path, registry(), custom=custom)
        try:
            assert isinstance([e async for e in runtime.stream("test")][-1], TurnCompleted)
            assert executions == expected and len(requests) == 2
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = create(tmp_path, registry(), thread=thread, custom=custom)
        try:
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert executions == expected and len(requests) == 3
            stored = await cold._repository.load_items(thread)
            calls = [i.call for i in stored if isinstance(i, ToolCallItem)]
            results = [i for i in stored if isinstance(i, ToolResultItem)]
            assert len(calls) == len(results) == 1
            assert calls[0].id == results[0].call_id == completed["call_id"]
            assert calls[0].name == completed["name"]
            assert calls[0].raw_arguments == (
                json.loads(completed["arguments"])["input"]
                if custom and case not in {"empty_name", "whitespace_name"}
                else completed["arguments"]
            )
            assert results[0].is_error is (case in {"empty_name", "whitespace_name"})
            for request in requests[1:]:
                kind = "function_call_output"
                outputs = [i for i in request["input"] if i.get("type") == kind]
                assert len(outputs) == 1 and outputs[0]["call_id"] == completed["call_id"]
        finally:
            await cold.aclose()

    asyncio.run(scenario())


BAD_ITEMS = [
    {"type": "message", "role": None, "content": [{"type": "output_text", "text": "BAD"}]},
    {"type": "message", "role": "assistant", "content": None},
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": 3}]},
    {"type": "message", "role": "assistant", "content": [{"type": "future", "text": "BAD"}]},
    {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "input_image", "image_url": "BAD", "detail": "future"}],
    },
    {"type": "reasoning", "summary": None, "encrypted_content": "BAD"},
    {"type": "reasoning", "summary": [{"type": "summary_text", "text": False}]},
    {"type": "reasoning", "summary": [], "content": [{"type": "future", "text": "BAD"}]},
    {"type": "function_call_output", "id": "BAD", "output": 3},
    {"type": "function_call_output", "id": "BAD", "output": [{"type": "input_text", "text": None}]},
    {"type": "web_search_call", "id": "BAD", "action": {"type": "search", "query": 3}},
    {"type": "web_search_call", "id": "BAD", "action": {"type": "search", "queries": [3]}},
    {
        "type": "tool_search_output",
        "id": "BAD",
        "execution": "server",
        "status": "completed",
        "tools": {},
    },
    {"type": "tool_search_call", "call_id": "BAD", "execution": None, "arguments": {}},
    {"type": "tool_search_call", "call_id": "BAD", "execution": "client"},
]


@pytest.mark.parametrize("boundary", ["done", "terminal"])
@pytest.mark.parametrize("bad", BAD_ITEMS)
def test_bad_nested_or_hosted_item_is_skipped_and_valid_later_item_survives(
    tmp_path, monkeypatch, boundary, bad
):
    async def scenario():
        requests = []
        good = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "GOOD"}],
        }

        def respond(request):
            requests.append(request)
            return response(
                [done(bad), done(good), terminal()]
                if boundary == "done"
                else [terminal([bad, good])]
            )

        install(monkeypatch, respond)
        runtime = create(tmp_path, ToolRegistry())
        try:
            rejected = bad["type"] not in {"message", "reasoning"}
            events = [e async for e in runtime.stream("test")]
            assert isinstance(events[-1], TurnFailed if rejected else TurnCompleted), events[-1]
            if rejected:
                assert not events[-1].retryable
            assert len(requests) == 1
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert "BAD" not in repr(stored)
            assert ("GOOD" in repr(stored)) is (not rejected)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("arguments", ["", "{", "[]", "null"])
def test_invalid_json_string_is_an_observation_not_an_empty_argument_call(
    tmp_path, monkeypatch, arguments
):
    async def scenario():
        requests, executions = [], []
        item = {
            "type": "function_call",
            "id": "item",
            "call_id": "call",
            "name": "guard",
            "arguments": arguments,
        }

        def respond(request):
            requests.append(request)
            return response([done(item), terminal()] if len(requests) == 1 else [terminal()])

        install(monkeypatch, respond)

        class Guard:
            spec = ToolSpec("guard", "guard", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "MUST_NOT_EXECUTE")

        registry = ToolRegistry()
        registry.register(Guard())
        runtime = create(tmp_path, registry)
        try:
            assert isinstance([e async for e in runtime.stream("test")][-1], TurnCompleted)
            assert len(requests) == 2 and not executions
            stored = await runtime._repository.load_items(runtime.thread_id)
            calls = [i for i in stored if isinstance(i, ToolCallItem)]
            results = [i for i in stored if isinstance(i, ToolResultItem)]
            assert len(calls) == len(results) == 1 and results[0].is_error
            assert calls[0].call.raw_arguments == arguments and calls[0].call.parse_error
            replay = json.loads(requests[1].content)["input"]
            replay_calls = [i for i in replay if i.get("type") == "function_call"]
            assert len(replay_calls) == 1 and replay_calls[0]["arguments"] == arguments
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["done", "terminal"])
@pytest.mark.parametrize("call_id", ["", "ordinary-call"])
@pytest.mark.parametrize("output_index", [None, 0])
@pytest.mark.parametrize("changed", [False, True])
def test_ordinary_freeform_call_identity_without_optional_item_id(
    tmp_path, monkeypatch, boundary, call_id, output_index, changed
):
    async def scenario():
        requests, executions = [], []
        item = {
            "type": "function_call",
            "call_id": call_id,
            "name": "guard",
            "arguments": '{"input":""}',
        }

        def respond(request):
            requests.append(json.loads(request.content))
            completed = done(item)
            if output_index is not None:
                completed["output_index"] = output_index
            packets = (
                [completed, completed, terminal([item])]
                if boundary == "done"
                else [terminal([item])]
            )
            if changed:
                replacement = {**item, "arguments": '{"input":"changed"}'}
                packets = (
                    [completed, {**completed, "item": replacement}, terminal([replacement])]
                    if boundary == "done"
                    else [completed, terminal([replacement])]
                )
            return response(packets if len(requests) == 1 else [terminal()])

        install(monkeypatch, respond)

        class Guard:
            spec = ToolSpec("guard", "guard", {}, input_kind="freeform")

            async def execute(self, call, context):
                executions.append((call.id, call.raw_arguments))
                return ToolResult(call.id, call.name, "empty custom input accepted")

        def registry():
            result = ToolRegistry()
            result.register(Guard())
            return result

        runtime = create(tmp_path, registry(), custom=True)
        try:
            events = [e async for e in runtime.stream("test")]
            assert isinstance(events[-1], TurnFailed if changed else TurnCompleted), events[-1]
            if changed:
                assert "changed" in events[-1].error and not events[-1].retryable
            assert executions == [(call_id, "")] and len(requests) == (1 if changed else 2)
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = create(tmp_path, registry(), custom=True, thread=thread)
        try:
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert executions == [(call_id, "")] and len(requests) == (2 if changed else 3)
            for request in requests[1:]:
                calls = [i for i in request["input"] if i.get("type") == "function_call"]
                results = [i for i in request["input"] if i.get("type") == "function_call_output"]
                assert len(calls) == len(results) == 1
                assert calls[0]["call_id"] == results[0]["call_id"] == call_id
                assert json.loads(calls[0]["arguments"]) == {"input": ""}
        finally:
            await cold.aclose()

    asyncio.run(scenario())
