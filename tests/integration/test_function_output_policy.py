import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.memory.transcript import render_transcript
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.protocol.tools import TextContent, ToolResult, ToolSpec
from corki.storage.sqlite import _result_from_json
from corki.tools import ToolRegistry


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("kind", ["text", "freeform", "error", "ordered"])
def test_function_raw_ledger_archive_and_http_history_are_distinct(tmp_path, api, kind):
    async def scenario():
        raw = "HEAD" + "字" * 30000 + "TAIL"
        requests, calls = [], []

        class Tool:
            spec = ToolSpec(
                "read", "read fixture", {}, input_kind="freeform" if kind == "freeform" else "json"
            )

            async def execute(self, call, context):
                calls.append(call)
                if kind == "error":
                    raise ValueError(raw)
                return ToolResult(
                    call.id,
                    call.name,
                    raw,
                    content_items=(TextContent(raw),) if kind == "ordered" else (),
                )

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            first = len(requests) == 1
            if api == "responses":
                output = (
                    [
                        {
                            "type": "function_call",
                            "call_id": "call-read",
                            "name": "read",
                            "arguments": json.dumps({"input": "raw"})
                            if kind == "freeform"
                            else "{}",
                        }
                    ]
                    if first
                    else [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r-{len(requests)}", "output": output},
                }
            else:
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-read",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": json.dumps({"input": "raw"})
                                    if kind == "freeform"
                                    else "{}",
                                },
                            }
                        ]
                    }
                    if first
                    else {"content": "done"}
                )
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        settings = CorkiSettings(
            working_directory=tmp_path,
            model="fixture",
            skills_enabled=False,
            api_mode=api,
            model_contexts=parse_model_contexts(
                {
                    "fixture": {
                        "context_window": 65536,
                        "truncation_policy": {"mode": "bytes", "limit": 100},
                    }
                }
            ),
        )

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Tool())
            adapter = OpenAIResponsesModel if api == "responses" else OpenAICompatibleModel
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "session.db",
                registry=registry,
                model=adapter(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    client=client,
                    capabilities=resolve_capabilities(
                        base_url="https://fixture.invalid/v1", api_mode=api
                    ),
                ),
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            events = [event async for event in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            originals = [
                item
                for item in await runtime._repository.load_items(thread)
                if isinstance(item, ToolResultItem)
            ]
            assert len(originals) == 1
            original = originals[0]
            assert original.content == ("ValueError: " + raw if kind == "error" else raw)
            assert raw in render_transcript((original,), redact=lambda text: text)
            import sqlite3

            with sqlite3.connect(tmp_path / "session.db") as connection:
                ledger = connection.execute(
                    "SELECT result_json FROM tool_executions WHERE call_id = ?", (str(calls[0].id),)
                ).fetchone()
            assert _result_from_json(ledger[0]).content == original.content
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([event async for event in cold.stream("continue")][-1], TurnCompleted)
            assert isinstance([event async for event in cold.compact()][-1], TurnCompleted)
            assert len(requests) == 4 and len(calls) == 1
            for request in requests[1:]:
                results = [
                    item
                    for item in request.get("input", request.get("messages", []))
                    if item.get("type") in {"function_call_output", "custom_tool_call_output"}
                    or item.get("role") == "tool"
                ]
                assert len(results) == 1
                output = results[0].get("output", results[0].get("content"))
                if isinstance(output, list):
                    output = output[0]["text"]
                assert "chars truncated" in output and output.endswith("TAIL") and len(output) < 200
            stored = [
                item
                for item in await cold._repository.load_items(thread)
                if isinstance(item, ToolResultItem)
            ]
            assert stored == originals
        finally:
            await cold.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("override", [None, 0])
def test_raw_result_survives_completed_ledger_fault_and_nested_call(tmp_path, nested, override):
    from corki.models import ModelCompleted
    from corki.protocol.events import TurnFailed
    from corki.protocol.ids import new_tool_call_id
    from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
    from corki.protocol.tools import ToolCall
    from corki.sessions import TurnStatus

    async def scenario():
        raw = "RAW" + "字" * 30000 + "END"
        calls, requests = [], []

        class Tool:
            spec = ToolSpec("read", "read fixture", {}, output_char_budget=80)

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, raw, fallback_token_limit_override=override)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="const value = await tools.read({}); text(value.length);",
                            input_kind="freeform",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), "read", {})
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    if nested:
                        assert str(len(raw)) in result.content
                    else:
                        assert len(result.content) <= 80
                        if override == 0:
                            assert result.content == "…22502 tokens truncated…"
                        else:
                            assert "characters omitted" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode" if nested else "direct",
            tool_output_token_limit=1000,
        )
        database = tmp_path / "session.db"

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Tool())
            return LangGraphRuntime.create(
                settings=settings,
                database_path=database,
                registry=registry,
                model=Model(),
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        append, save = runtime._repository.append_items, runtime._repository.save_turn
        injected = False

        async def fail_append(target, items):
            nonlocal injected
            if not injected and any(isinstance(item, ToolResultItem) for item in items):
                injected = True
                raise OSError("fault after completed tool ledger before conversation append")
            await append(target, items)

        async def leave_running(record):
            if record.status is TurnStatus.FAILED:
                raise OSError("leave crash-recovery turn pending")
            await save(record)

        runtime._repository.append_items = fail_append
        runtime._repository.save_turn = leave_running
        try:
            events = [event async for event in runtime.stream("read")]
            assert isinstance(events[-1], TurnFailed) and injected
            assert len(requests) == 1 and len(calls) == 1
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([event async for event in cold.resume_pending()][-1], TurnCompleted)
            assert len(requests) == 2 and len(calls) == 1
            import sqlite3

            with sqlite3.connect(database) as connection:
                (payload,) = connection.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='read'"
                ).fetchone()
            result = _result_from_json(payload)
            assert result.content == raw and result.fallback_token_limit_override == override
            if not nested:
                original = [
                    item
                    for item in await cold._repository.load_items(thread)
                    if isinstance(item, ToolResultItem)
                ][0]
                assert original.content == raw and not original.model_output_projected
        finally:
            await cold.aclose()

    asyncio.run(scenario())
