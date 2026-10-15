"""Raw tool inputs survive the real provider/Runtime/history round trip."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolCallItem, ToolResultItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry

SOURCE = '// @exec: {"max_output_tokens": 100}\nconst x = `原样`;\ntext(x);\n'
GRAMMAR = {"type": "grammar", "syntax": "lark", "definition": "start: /[\\s\\S]+/"}


@pytest.mark.parametrize("mode", ["native", "compatible", "chat"])
@pytest.mark.parametrize("source", [SOURCE, ""])
def test_raw_tool_round_trip_and_durable_replay(tmp_path, monkeypatch, mode, source):
    async def scenario():
        requests, calls = [], []

        class RawTool:
            spec = ToolSpec(
                "raw_probe", "raw fixture", {}, input_kind="freeform", freeform_format=GRAMMAR
            )

            async def execute(self, call, context):
                calls.append(call)
                assert call.input_kind == "freeform"
                assert call.arguments is None
                assert call.raw_arguments == source
                return ToolResult(call.id, call.name, "received raw")

        def handle(request):
            assert str(request.url) == "https://fixture.invalid/v1/" + (
                "chat/completions" if mode == "chat" else "responses"
            )
            payload = json.loads(request.content)
            requests.append(payload)
            if mode == "chat":
                if len(requests) == 1:
                    definition = next(
                        tool["function"]
                        for tool in payload["tools"]
                        if tool["function"]["name"] == "raw_probe"
                    )
                    assert definition["parameters"]["properties"]["input"]["type"] == "string"
                    delta = {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c",
                                "function": {
                                    "name": "raw_probe",
                                    "arguments": json.dumps({"input": source}),
                                },
                            }
                        ]
                    }
                    finish = "tool_calls"
                else:
                    call = next(
                        message["tool_calls"][0]
                        for message in payload["messages"]
                        if message.get("tool_calls")
                    )
                    assert json.loads(call["function"]["arguments"]) == {"input": source}
                    assert any(
                        message.get("content") == "received raw" for message in payload["messages"]
                    )
                    delta, finish = {"content": "done"}, "stop"
                return httpx.Response(
                    200,
                    text="data: "
                    + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]})
                    + "\n\ndata: [DONE]\n\n",
                )
            if len(requests) == 1:
                definition = next(
                    tool for tool in payload["tools"] if tool.get("name") == "raw_probe"
                )
                assert definition["type"] == "function" and "format" not in definition
                assert definition["parameters"]["required"] == ["input"]
                item = {
                    "type": "function_call",
                    "id": "i",
                    "call_id": "c",
                    "name": "raw_probe",
                    "arguments": json.dumps({"input": source}),
                }
                body = (
                    "data: "
                    + json.dumps({"type": "response.output_item.done", "item": item})
                    + "\n\n"
                )
            else:
                body = ""
                call = next(item for item in payload["input"] if item.get("call_id") == "c")
                assert call["type"] == "function_call"
                assert json.loads(call["arguments"]) == {"input": source}
                assert any(
                    item.get("type") == "function_call_output" and item["output"] == "received raw"
                    for item in payload["input"]
                )
            body += 'data: {"type":"response.completed","response":{"id":"r"}}\n\n'
            return httpx.Response(200, text=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda **kwargs: client)
        registry = ToolRegistry()
        registry.register(RawTool())
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="chat_completions" if mode == "chat" else "responses",
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                tool_freeform_mode="native" if mode == "native" else "compatible",
            ),
            database_path=repository.path,
            repository=repository,
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(calls) == 1
            assert len(requests) == 2
            reopened = SQLiteSessionRepository(repository.path)
            items = await reopened.load_items(events[-1].thread_id)
            saved_call = next(item.call for item in items if isinstance(item, ToolCallItem))
            saved_result = next(item for item in items if isinstance(item, ToolResultItem))
            assert saved_call == calls[0]
            assert saved_result.input_kind == "freeform"
            # Rebuild both Runtime and HTTP client; read-only SQLite inspection
            # alone does not prove cold history reaches a new model request.
            settings, thread = runtime._settings, runtime.thread_id
            await runtime.aclose()
            await client.aclose()
            client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            fresh_registry = ToolRegistry()
            fresh_registry.register(RawTool())
            runtime = await LangGraphRuntime.acreate(
                settings=settings,
                database_path=repository.path,
                registry=fresh_registry,
                thread_id=thread,
            )
            resumed = [event async for event in runtime.stream("continue")]
            assert isinstance(resumed[-1], TurnCompleted), resumed[-1]
            assert len(requests) == 3 and len(calls) == 1
            restored = await runtime._repository.load_items(thread)
            assert sum(isinstance(item, ToolCallItem) for item in restored) == 1
            assert sum(isinstance(item, ToolResultItem) for item in restored) == 1
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
