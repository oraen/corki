"""Deferred raw tools must be discovered and run through actual Runtime dispatch."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


def packet(kind, **fields):
    return "data: " + json.dumps({"type": kind, **fields}) + "\n\n"


@pytest.mark.parametrize("search", ["native", "compatible"])
@pytest.mark.parametrize("freeform", ["native", "compatible"])
def test_search_then_raw_call_retains_wire_kind_and_durable_definition(
    tmp_path, monkeypatch, search, freeform
):
    async def scenario():
        requests, calls = [], []
        grammar = {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"}

        class Tool:
            spec = ToolSpec(
                "raw_probe",
                "execute raw source",
                {},
                exposure=ToolExposure.DEFERRED,
                input_kind="freeform",
                freeform_format=grammar,
            )

            async def execute(self, call, context):
                calls.append(call)
                assert (call.arguments, call.raw_arguments, call.input_kind) == (
                    None,
                    "text('ok')",
                    "freeform",
                )
                return ToolResult(call.id, call.name, "raw executed")

        def handle(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            payload = json.loads(request.content)
            requests.append(payload)
            assert all(tool["type"] == "function" for tool in payload["tools"])
            assert not any(
                item.get("type")
                in {"tool_search_output", "custom_tool_call", "custom_tool_call_output"}
                for item in payload["input"]
            )
            if len(requests) == 1:
                assert not any(tool.get("name") == "raw_probe" for tool in payload["tools"])
                item = {"id": "s", "call_id": "search"}
                item.update(
                    type="function_call",
                    name="tool_search",
                    arguments=json.dumps({"query": "raw_probe"}),
                )
            elif len(requests) == 2:
                definition = next(
                    tool for tool in payload["tools"] if tool.get("name") == "raw_probe"
                )
                item = {"id": "r", "call_id": "raw", "name": "raw_probe"}
                assert definition["type"] == "function" and "format" not in definition
                assert definition["parameters"]["required"] == ["input"]
                item.update(type="function_call", arguments=json.dumps({"input": "text('ok')"}))
            else:
                assert any(item.get("output") == "raw executed" for item in payload["input"])
                return httpx.Response(
                    200, text=packet("response.completed", response={"id": "done"})
                )
            return httpx.Response(
                200,
                text=packet("response.output_item.done", item=item)
                + packet("response.completed", response={"id": "done"}),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda **kwargs: client)
        registry = ToolRegistry()
        registry.register(Tool())
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                tool_search_mode=search,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                tool_freeform_mode=freeform,
            ),
            database_path=repository.path,
            repository=repository,
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3 and len(calls) == 1
            reopened = SQLiteSessionRepository(repository.path)
            items = await reopened.load_items(events[-1].thread_id)
            result = next(
                item
                for item in items
                if isinstance(item, ToolResultItem) and item.tool_name == "tool_search"
            )
            assert result.discovered_tools == (Tool.spec,)
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "arguments", ["{}", '{"input":7}', '{"input":"ok","extra":1}', "{bad", "[]", None]
)
def test_bad_wrapper_is_observation_even_with_legacy_native_configuration(
    tmp_path, monkeypatch, arguments
):
    async def scenario():
        requests, calls = [], []

        class Tool:
            spec = ToolSpec("raw", "fixture", {}, input_kind="freeform")

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "unexpected")

        def handle(request):
            requests.append(json.loads(request.content))
            body = ""
            if len(requests) == 1:
                body += packet(
                    "response.output_item.done",
                    item={
                        "type": "function_call",
                        "id": "i",
                        "call_id": "c",
                        "name": "raw",
                        "arguments": arguments or "{}",
                    },
                )
            else:
                assert any(
                    "invalid JSON arguments" in str(item.get("output", ""))
                    for item in requests[-1]["input"]
                )
            return httpx.Response(
                200, text=body + packet("response.completed", response={"id": "done"})
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda **kwargs: client)
        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                tool_freeform_mode="native" if arguments is None else "compatible",
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert calls == []
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
