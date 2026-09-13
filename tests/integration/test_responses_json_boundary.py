import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import CompactionItem, UserMessageItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


def config(tmp_path, *, legacy=False):
    return CorkiSettings(
        tmp_path,
        api_mode="responses",
        provider_name="openai",
        api_key="fixture",
        api_base="https://fixture.invalid/v1",
        skills_enabled=False,
        remote_compaction_v2=not legacy,
        model_request_max_retries=0,
        model_max_retries=0,
        model_retry_base_seconds=0.001,
    )


def install(monkeypatch, respond):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        http_client,
        "OwnedHTTPClient",
        lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
    )


def message(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


@pytest.mark.parametrize("v2", [False, True])
@pytest.mark.parametrize(
    "extra",
    [
        ',"unknown":NaN',
        ',"\\ud800":0',
        ',"unknown":Infinity',
        ',"delta":3',
        ',"summary_index":1.5',
        ',"content_index":true',
        ',"item_id":[]',
        ',"delta":null,"delta":"duplicate"',
        ',"metadata":{"text":"\\ud800"}',
    ],
)
def test_invalid_envelope_does_not_execute_or_count_output(tmp_path, monkeypatch, v2, extra):
    async def scenario():
        requests, executions = [], []
        bad_item = (
            message("BAD")
            if v2
            else {
                "type": "function_call",
                "name": "guard",
                "call_id": "bad",
                "arguments": "{}",
            }
        )
        good_item = message("GOOD" if v2 else "done")
        bad = json.dumps({"type": "response.output_item.done", "item": bad_item})[:-1] + extra + "}"
        good = json.dumps({"type": "response.output_item.done", "item": good_item})
        terminal = json.dumps({"type": "response.completed", "response": {"id": "done"}})

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(request)
            events = [bad, good, terminal] if len(requests) == 1 else [good, terminal]
            return httpx.Response(200, text="".join(f"data: {event}\n\n" for event in events))

        install(monkeypatch, respond)

        class Guard:
            spec = ToolSpec("guard", "guard", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "must not execute")

        registry = ToolRegistry()
        registry.register(Guard())
        runtime = LangGraphRuntime.create(
            settings=config(tmp_path), database_path=tmp_path / "s.db", registry=registry
        )
        try:
            events = [
                event async for event in (runtime.compact() if v2 else runtime.stream("test"))
            ]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 1 and not executions
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert "BAD" not in repr(stored)
            if v2:
                marker = next(item for item in stored if isinstance(item, CompactionItem))
                assert marker.summary == "GOOD" and marker.remote_payload_json is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["legacy", "v2", "normal"])
def test_valid_duplicate_policy_survives_runtime_and_cold_replay(tmp_path, monkeypatch, mode):
    async def scenario():
        requests, executions = [], []
        terminal = 'data: {"type":"response.completed","response":{"id":"terminal"}}\n\n'

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(json.loads(request.content))
            if len(requests) > 1:
                return httpx.Response(200, text=terminal)
            item = (
                '{"type":"message","role":"assistant",'
                '"content":[{"type":"output_text","text":"old"}],'
                '"content":[{"type":"output_text","text":"FINAL"}],'
                '"unknown":1e999,"unknown":2}'
                if mode != "normal"
                else '{"type":"function_call","name":"wrong","name":"guard",'
                '"call_id":"one","arguments":"bad","arguments":"{}"}'
            )
            return httpx.Response(
                200,
                text='data: {"type":"response.output_item.done","unknown":1e999,"item":'
                + item
                + "}\n\n"
                + terminal,
            )

        install(monkeypatch, respond)

        class Guard:
            spec = ToolSpec("guard", "guard", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "observation")

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Guard())
            return LangGraphRuntime.create(
                settings=config(tmp_path, legacy=mode == "legacy"),
                database_path=tmp_path / "s.db",
                registry=registry,
                thread_id=thread,
            )

        runtime = create()
        try:
            if mode != "normal":
                await runtime._ensure_ready()
                await runtime._repository.append_items(
                    runtime.thread_id, (UserMessageItem("old", "old"),)
                )
            events = [
                e
                async for e in (
                    runtime.stream("call guard") if mode == "normal" else runtime.compact()
                )
            ]
            assert isinstance(events[-1], TurnCompleted)
            assert executions == (["one"] if mode == "normal" else [])
            thread = runtime.thread_id
            before = await runtime._repository.load_items(thread)
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            if mode != "normal":
                assert "FINAL" in json.dumps(requests[-1])
                marker = next(i for i in before if isinstance(i, CompactionItem))
                assert marker.summary == "FINAL" and marker.remote_payload_json is None
            assert executions == (["one"] if mode == "normal" else [])
            stored = await cold._repository.load_items(thread)
            assert stored[: len(before)] == before
            assert len(requests) == (3 if mode == "normal" else 2)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "body",
    [
        b'{"output":[],"output":[]}',
        b'{"output":[{"type":"compaction","encrypted_content":"first","encrypted_content":"last"}]}',
        b'{"output":[{"type":"compaction","type":"compaction","encrypted_content":"x"}]}',
        b'{"output":[{"type":"message","role":"user","content":[{"type":"input_text","text":"a","text":"b"}]}]}',
        b'{"output":[{"type":"compaction","encrypted_content":"x","internal_chat_message_metadata_passthrough":{"turn_id":null,"turn_id":"second"}}]}',
        b'{"output":[{"type":"compaction","encrypted_content":"\\ud800"}]}',
        '{"output":[]}'.encode("utf-16"),
        b'\xef\xbb\xbf{"output":[]}',
    ],
)
def test_legacy_json_body_cannot_replace_ordinary_stream_completion(tmp_path, monkeypatch, body):
    async def scenario():
        requests = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(request)
            return httpx.Response(200, content=body)

        install(monkeypatch, respond)
        runtime = LangGraphRuntime.create(
            settings=config(tmp_path, legacy=True),
            database_path=tmp_path / "s.db",
            registry=ToolRegistry(),
        )
        try:
            await runtime._ensure_ready()
            old = UserMessageItem("preserve original", "old")
            await runtime._repository.append_items(runtime.thread_id, (old,))
            events = [event async for event in runtime.compact()]
            assert isinstance(events[-1], TurnFailed)
            assert await runtime._repository.load_items(runtime.thread_id) == (old,)
            assert len(requests) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
