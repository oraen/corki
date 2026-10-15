"""Memory contracts through ordinary provider adapters, dispatch and real Code Mode."""

import asyncio
import json
import sqlite3
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.memory import LocalMemoryBackend
from corki.models import (
    ModelCompleted,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry

CASES = [
    ("list", {"max_results": 0}, False),
    ("list", {"cursor": "+0001", "max_results": 1}, False),
    ("list", {"path": None, "cursor": None, "max_results": None}, False),
    ("list", {"max_results": 2**64 - 1}, False),
    ("list", " \n\u00a0 ", False),
    ("read", {"path": "a.md", "line_offset": None, "max_lines": None}, False),
    ("read", '{"path":"missing","path":"a.md"}', False),
    (
        "search",
        {"queries": ["alpha", "beta"], "match_mode": {"type": "all_within_lines", "line_count": 2}},
        False,
    ),
    ("search", {"queries": ["ALPHA"], "case_sensitive": None, "normalized": None}, False),
    ("search", {"queries": ["alpha"], "match_mode": {"type": "any", "ignored": True}}, False),
    ("add_ad_hoc_note", {"filename": "2026-09-08T12-00-00--.md", "note": " exact\n"}, False),
    ("list", {"cursor": 1}, True),
    ("list", {"cursor": "１２"}, True),
    ("list", {"max_results": 2**64}, True),
    ("list", {"max_results": True}, True),
    ("list", {"max_results": 1.0}, True),
    ("list", {"max_results": -1}, True),
    ("list", {"limit": 1}, True),
    ("list", "\u001c", True),
    ("read", {"path": "a.md", "max_tokens": 1}, True),
    ("read", {"path": "a.md", "line_offset": 0}, True),
    ("search", {"queries": ["alpha"], "match_mode": "any"}, True),
    (
        "search",
        {"queries": ["alpha"], "match_mode": {"type": "all_within_lines", "line_count": 0}},
        True,
    ),
    ("search", {"queries": [None]}, True),
    ("search", {"queries": ["alpha"], "within_lines": 2}, True),
]


@pytest.mark.parametrize("mode", ["chat", "responses", "native", "lite"])
@pytest.mark.parametrize("leaf,arguments,is_error", CASES)
def test_memory_tool_contract_on_real_http_requests(tmp_path, mode, leaf, arguments, is_error):
    async def scenario():
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        (root / "a.md").write_text("alpha\nbeta\n", encoding="utf-8")
        (root / "b.md").write_text("other\n", encoding="utf-8")
        bodies = []
        name = "memories::" + leaf
        wire_name = compatible_tool_name(name)
        raw = arguments if isinstance(arguments, str) else json.dumps(arguments)

        def handle(request):
            endpoint = "chat/completions" if mode == "chat" else "responses"
            assert str(request.url) == f"https://fixture.invalid/v1/{endpoint}"
            assert "x-openai-internal-codex-responses-lite" not in request.headers
            body = json.loads(request.content)
            assert all(tool["type"] == "function" for tool in body["tools"])
            bodies.append(body)
            first = len(bodies) == 1
            if mode == "chat":
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "memory-call",
                                "type": "function",
                                "function": {"name": wire_name, "arguments": raw},
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
            else:
                item = {
                    "type": "function_call",
                    "call_id": "memory-call",
                    "name": wire_name,
                    "arguments": raw,
                }
                output = (
                    [item]
                    if first
                    else [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
                packet = {"type": "response.completed", "response": {"id": "r", "output": output}}
            return httpx.Response(200, text="data: " + json.dumps(packet) + "\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        api_mode = "chat_completions" if mode == "chat" else "responses"
        model_type = OpenAICompatibleModel if mode == "chat" else OpenAIResponsesModel
        model = model_type(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode=api_mode),
                supports_native_namespaces=mode == "native",
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                model="fixture",
                api_mode=api_mode,
                api_base="https://fixture.invalid/v1",
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_dedicated_tools=True,
                tool_namespace_mode="native" if mode == "native" else "compatible",
                model_contexts=parse_model_contexts(
                    {"fixture": {"context_window": 272000, "use_responses_lite": mode == "lite"}}
                ),
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            model=model,
        )
        try:
            events = [
                e async for e in runtime.stream("inspect memory; remember the note if requested")
            ]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(bodies) == 2
            definitions = {
                t.get("function", t)["name"]: t.get("function", t) for t in bodies[0]["tools"]
            }
            definition = definitions[wire_name]
            assert "output_schema" not in definition
            assert definition["parameters"]["additionalProperties"] is False
            items = await runtime._repository.load_items(runtime.thread_id)
            result = next(i for i in items if isinstance(i, ToolResultItem))
            assert result.tool_name == name and result.is_error == is_error, result
            assert json.dumps(result.content) in json.dumps(bodies[1])
            if not is_error:
                value = json.loads(result.content)
                with sqlite3.connect(tmp_path / "sessions.db") as db:
                    payload = json.loads(
                        db.execute(
                            "SELECT result_json FROM tool_executions WHERE call_id='memory-call'"
                        ).fetchone()[0]
                    )
                assert payload["code_mode_output"]["value"] == value
                if leaf == "read":
                    assert value == {
                        "path": "a.md",
                        "start_line_number": 1,
                        "content": "alpha\nbeta\n",
                        "truncated": False,
                    }
                    assert "max_tokens" not in definition["parameters"]["properties"]
                elif leaf == "list":
                    assert all(set(row) == {"path", "entry_type"} for row in value["entries"])
                    if isinstance(arguments, dict) and arguments.get("max_results") == 0:
                        assert value["next_cursor"] == "1" and value["truncated"]
                    if isinstance(arguments, dict) and arguments.get("cursor") == "+0001":
                        assert value["entries"][0]["path"] == "b.md" and value["next_cursor"] == "2"
                elif leaf == "search":
                    assert isinstance(value["match_mode"], dict)
                    if arguments.get("queries") == ["ALPHA"]:
                        assert value["matches"] == [], "null case_sensitive defaults to true"
                    else:
                        assert value["matches"][0]["path"] == "a.md"
                    assert "ignored" not in value["match_mode"]
                else:
                    assert value == {}
                    assert (
                        root / "extensions/ad_hoc/notes/2026-09-08T12-00-00--.md"
                    ).read_bytes() == b" exact\n"
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
def test_native_memory_values_and_semantic_errors_in_code_mode(tmp_path, mode):
    async def scenario():
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        (root / "a.md").write_text("alpha\nbeta\n", encoding="utf-8")
        (root / "z-large.md").write_text("BEGIN\n" + "界" * 40000 + "\nEND\n", encoding="utf-8")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    script = (
                        "const r=await tools.memories__read({path:'a.md'});"
                        "const l=await tools.memories__list({max_results:0});"
                        "const s=await tools.memories__search({queries:['alpha']});"
                        "text({kind:typeof r,content:r.content,cursor:l.next_cursor,"
                        "entry:l.entries[0].entry_type,match:s.matches[0].path});"
                        "try {await tools.memories__read({path:'missing'});} "
                        "catch(e) {text('caught missing');}"
                        "const big=await tools.memories__read({path:'z-large.md'});"
                        "text({bounded:big.truncated && big.content.length>26000 "
                        "&& big.content.length<30000 && big.content.startsWith('BEGIN') "
                        "&& big.content.endsWith('END\\n')});"
                    )
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        raw_arguments=script,
                        input_kind="freeform",
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_dedicated_tools=True,
                tool_mode=mode,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("use memory in code")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            result = next(
                i
                for i in requests[-1].items
                if isinstance(i, ToolResultItem) and i.tool_name == "exec"
            )
            assert '"kind":"object"' in result.content and '"entry":"file"' in result.content
            assert '"cursor":"1"' in result.content and "caught missing" in result.content
            assert '"bounded":true' in result.content
            assert (
                "output_schema"
                in next(s for s in requests[0].tools if s.name == "exec").description
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
def test_memory_tools_are_exclusive_even_when_model_requests_concurrency(
    tmp_path, monkeypatch, mode
):
    async def scenario():
        trace, requests = [], []

        async def read(backend, path, **kwargs):
            trace.append(("start", path))
            await asyncio.sleep(0)
            trace.append(("end", path))
            return {"path": path, "start_line_number": 1, "content": "ok", "truncated": False}

        monkeypatch.setattr(LocalMemoryBackend, "read", read)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    calls = [
                        ToolCall(new_tool_call_id(), "memories::read", {"path": path})
                        for path in ("a", "b")
                    ]
                    if mode != "direct":
                        calls = [
                            ToolCall(
                                new_tool_call_id(),
                                "exec",
                                None,
                                input_kind="freeform",
                                raw_arguments=(
                                    "await Promise.all([tools.memories__read({path:'a'}),"
                                    "tools.memories__read({path:'b'})]);"
                                ),
                            )
                        ]
                    yield ModelCompleted(tuple(ToolCallItem(call, turn, step) for call in calls))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_dedicated_tools=True,
                tool_mode=mode,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=tmp_path / "memories",
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("read both")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert trace == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_legacy_completed_call_is_not_rebound_and_new_call_can_use_native_tool(tmp_path):
    async def scenario():
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        (root / "a.md").write_text("native fact", encoding="utf-8")
        legacy_calls = []

        class Legacy:
            spec = ToolSpec("memory_read", "old fixture", {"type": "object"})

            async def execute(self, call, context):
                legacy_calls.append(call.id)
                return ToolResult(call.id, call.name, "old saved result")

        class Model:
            def __init__(self, legacy):
                self.legacy = legacy
                self.requests = []

            async def stream(self, request):
                self.requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                count = len(self.requests)
                if count == 1:
                    name = "memory_read"
                elif not self.legacy and count == 2:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert result.is_error and "advertised" in result.content
                    name = "memories::read"
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), name, {"path": "a.md"}), turn, step
                        ),
                    )
                )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Legacy())
        first = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(True),
        )
        try:
            assert isinstance([e async for e in first.stream("old call")][-1], TurnCompleted)
            original = await first._repository.load_items(first.thread_id)
        finally:
            await first.aclose()
        model = Model(False)
        second = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_dedicated_tools=True,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            thread_id=first.thread_id,
            model=model,
        )
        try:
            assert [e async for e in second.resume_pending()] == []
            events = [e async for e in second.stream("continue with native memory")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            items = await second._repository.load_items(first.thread_id)
            assert items[: len(original)] == original and len(legacy_calls) == 1
            result = next(i for i in reversed(items) if isinstance(i, ToolResultItem))
            assert not result.is_error and "native fact" in result.content
            assert len(model.requests) == 3
        finally:
            await second.aclose()

    asyncio.run(scenario())
