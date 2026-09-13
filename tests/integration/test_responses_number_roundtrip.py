import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import (
    CompactionItem,
    HostedToolItem,
    RemoteHistoryItem,
    ToolResultItem,
    UserMessageItem,
)
from corki.protocol.tools import ToolExposure, ToolSpec
from corki.protocol.wire_json import loads_wire
from corki.protocol.wire_numbers import dumps_wire
from corki.tools import ToolRegistry

METADATA = "internal_chat_message_metadata_passthrough"
TERMINAL = 'data: {"type":"response.completed","response":{"id":"terminal"}}\n\n'
NUMBERS = [
    ("1.234567890123456789", "1.234567890123456789"),
    ("1e999", "1e+999"),
    ("-1E-9999", "-1e-9999"),
    ("0e999", "0e+999"),
    ("1.2300", "1.2300"),
    ("1e007", "1e+007"),
    ("340282366920938463463374607431768211457", "340282366920938463463374607431768211457"),
]


class NumericToken(str):
    pass


def exact_loads(raw):
    return json.loads(raw, parse_float=NumericToken, parse_int=NumericToken)


@pytest.mark.parametrize(
    "legacy,kind", [(False, "compaction"), (True, "compaction"), (True, "message")]
)
@pytest.mark.parametrize("token,canonical", NUMBERS)
def test_exact_archived_numbers_are_preserved_without_reenabling_remote_compaction(
    tmp_path, monkeypatch, legacy, kind, token, canonical
):
    async def scenario():
        prefix = (
            '"type":"compaction","encrypted_content":"EXACT"'
            if kind == "compaction"
            else '"type":"message","role":"user","content":[{"type":"input_text","text":"EXACT"}]'
        )
        item = "{" + prefix + ',"' + METADATA + '":{"create_time":' + token + "}}"
        requests = []
        phase = "initial"
        failed = set()

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append((phase, request.content))
            assert request.headers["content-type"] == "application/json"
            if phase != "initial" and phase not in failed:
                failed.add(phase)
                return httpx.Response(500, text="transient")
            if phase == "next":
                return httpx.Response(200, text=TERMINAL)
            return httpx.Response(
                200,
                text='data: {"type":"response.output_item.done","item":'
                + json.dumps(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "SUMMARY"}],
                    }
                )
                + "}\n\n"
                + TERMINAL,
            )

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    tmp_path,
                    api_mode="responses",
                    provider_name="openai",
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    remote_compaction_v2=not legacy,
                    model_request_max_retries=1,
                    model_max_retries=0,
                    model_retry_base_seconds=0.001,
                ),
                database_path=tmp_path / "s.db",
                registry=ToolRegistry(),
                thread_id=thread,
            )

        runtime = create()
        try:
            await runtime._ensure_ready()
            payload = dumps_wire(loads_wire(item))
            archived = (
                RemoteHistoryItem(payload, "old")
                if legacy
                else CompactionItem("", None, "old", remote_payload_json=payload)
            )
            await runtime._repository.append_items(
                runtime.thread_id, (UserMessageItem("old", "old"), archived)
            )
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnFailed if kind == "compaction" else TurnCompleted)
            if kind == "compaction":
                assert not requests and "dedicated compaction history" in events[-1].error
            stored = await runtime._repository.load_items(runtime.thread_id)
            saved = {
                i.id: i.payload_json if isinstance(i, RemoteHistoryItem) else i.remote_payload_json
                for i in stored
                if isinstance(i, RemoteHistoryItem)
                or isinstance(i, CompactionItem)
                and i.remote_payload_json is not None
            }
            assert len(saved) == 1
            assert exact_loads(next(iter(saved.values())))[METADATA] == {"create_time": canonical}
            assert isinstance(
                exact_loads(next(iter(saved.values())))[METADATA]["create_time"], NumericToken
            )
            thread = runtime.thread_id
        finally:
            await runtime.aclose()

        cold = create(thread)
        try:
            for phase in ("next", "recompact"):
                events = [
                    e
                    async for e in (cold.stream("continue") if phase == "next" else cold.compact())
                ]
                if kind == "compaction":
                    assert isinstance(events[-1], TurnFailed)
                    assert "dedicated compaction history" in events[-1].error
                    assert not requests
                    continue
                assert isinstance(events[-1], TurnCompleted)
                attempts = [body for stage, body in requests if stage == phase]
                assert len(attempts) == 2 and attempts[0] == attempts[1]
                history = exact_loads(attempts[-1])["input"]
                assert all(METADATA not in item for item in history)
                assert b"Infinity" not in attempts[-1] and b"NaN" not in attempts[-1]
            stored = await cold._repository.load_items(thread)
            for entry in stored:
                if entry.id in saved:
                    raw = (
                        entry.payload_json
                        if isinstance(entry, RemoteHistoryItem)
                        else entry.remote_payload_json
                    )
                    assert raw == saved[entry.id]
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["web_search_call", "function_call_output"])
@pytest.mark.parametrize("token", ["1.234567890123456789", "1e+999"])
def test_archived_hosted_numbers_remain_private_during_ordinary_cold_replay(
    tmp_path, monkeypatch, kind, token
):
    async def scenario():
        base = {"type": kind, "id": "hosted"}
        if kind == "web_search_call":
            base["action"] = {"type": "search", "query": "query"}
        else:
            base["output"] = "x" * 200
        raw = json.dumps(base)[:-1] + ',"' + METADATA + '":{"create_time":' + token + "}}"
        requests = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(exact_loads(request.content))
            return httpx.Response(200, text=TERMINAL)

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    tmp_path,
                    model="fixture",
                    api_mode="responses",
                    provider_name="openai",
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    model_contexts=parse_model_contexts(
                        {
                            "fixture": {
                                "context_window": 65536,
                                "truncation_policy": {"mode": "bytes", "limit": 10},
                            }
                        }
                    ),
                ),
                database_path=tmp_path / "s.db",
                registry=ToolRegistry(),
                thread_id=thread,
            )

        runtime = create()
        try:
            await runtime._ensure_ready()
            archived = HostedToolItem(raw, "old", "old-step")
            await runtime._repository.append_items(runtime.thread_id, (archived,))
            assert isinstance([e async for e in runtime.stream("receive")][-1], TurnCompleted)
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("recall")][-1], TurnCompleted)
            assert len(requests) == 2
            sent = next(i for i in requests[-1]["input"] if "External hosted-tool event" in str(i))
            assert METADATA not in json.dumps(sent)
            assert not any(i.get("type") == kind for i in requests[-1]["input"])
            saved = [
                i
                for i in await cold._repository.load_items(thread)
                if isinstance(i, HostedToolItem)
            ]
            assert len(saved) == 1
            assert saved[0] == archived
            assert exact_loads(saved[0].payload_json)[METADATA] == {"create_time": token}
            if kind == "function_call_output":
                assert exact_loads(saved[0].payload_json)["output"] == "x" * 200
                assert "truncated" in sent["content"]
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("token", [token for token, _ in NUMBERS])
def test_ordinary_search_error_observation_keeps_original_numeric_arguments(
    tmp_path, monkeypatch, token
):
    async def scenario():
        requests, executions = [], []

        class Deferred:
            spec = ToolSpec(
                "deferred", "deferred", {"type": "object"}, exposure=ToolExposure.DEFERRED
            )

            async def execute(self, call, context):
                executions.append(call.id)
                raise AssertionError("invalid search must not invoke this tool")

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(exact_loads(request.content))
            text = (
                'data: {"type":"response.output_item.done","item":'
                + json.dumps(
                    {
                        "type": "function_call",
                        "call_id": "bad-search",
                        "name": "tool_search",
                        "arguments": '{"query":' + token + "}",
                    }
                )
                + "}\n\n"
                if len(requests) == 1
                else ""
            )
            return httpx.Response(200, text=text + TERMINAL)

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Deferred())
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    tmp_path,
                    api_mode="responses",
                    provider_name="openai",
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    tool_search_mode="native",
                ),
                database_path=tmp_path / "s.db",
                registry=registry,
                thread_id=thread,
            )

        runtime = create()
        try:
            assert isinstance([e async for e in runtime.stream("search")][-1], TurnCompleted)
            assert len(requests) == 2
            stored = await runtime._repository.load_items(runtime.thread_id)
            results = [i for i in stored if isinstance(i, ToolResultItem)]
            assert len(results) == 1 and results[0].is_error
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("continue")][-1], TurnCompleted)
            assert len(requests) == 3 and not executions
            for body in requests[1:]:
                search = next(i for i in body["input"] if i.get("type") == "function_call")
                assert search["name"] == "tool_search"
                assert exact_loads(search["arguments"]) == {"query": token}
                assert isinstance(exact_loads(search["arguments"])["query"], NumericToken)
        finally:
            await cold.aclose()

    asyncio.run(scenario())
