"""Dispatch failures reject JS promises; error-valued tool results do not."""

import asyncio
import json
import sqlite3

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import ToolCallCompleted, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import CodeModeOutput, ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools import FatalToolError, ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


@pytest.mark.parametrize("flavor", ["call_id", "tool_name", "nan", "infinity", "dict", "none"])
@pytest.mark.parametrize("streamed", [False, True])
def test_invalid_nested_result_does_not_poison_parallel_sibling(tmp_path, flavor, streamed):
    async def scenario():
        requests, calls = [], []
        admitted = asyncio.Event()

        class Probe:
            spec = ToolSpec(
                "probe",
                "result boundary",
                {"type": "object"},
                concurrency=ToolConcurrency.PARALLEL,
            )

            async def execute(self, call, context):
                calls.append(call)
                if call.arguments["valid"]:
                    admitted.set()
                    return ToolResult(call.id, call.name, "sibling completed")
                await asyncio.wait_for(admitted.wait(), timeout=5)
                if flavor == "dict":
                    return {"content": "invalid result"}
                if flavor == "none":
                    return None
                return ToolResult(
                    "wrong-id" if flavor == "call_id" else call.id,
                    "wrong-tool" if flavor == "tool_name" else call.name,
                    "invalid result",
                    mcp_result_json=(
                        '{"content":[],"value":NaN}'
                        if flavor == "nan"
                        else '{"content":[],"value":Infinity}'
                        if flavor == "infinity"
                        else None
                    ),
                )

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    item = ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=(
                                "const results = await Promise.allSettled(["
                                "tools.probe({valid:false}), tools.probe({valid:true})]);"
                                "text(results.map(r => r.status)); text(results[1].value);"
                            ),
                        ),
                        request.items[-1].turn_id,
                        new_step_id(),
                    )
                    if streamed:
                        yield ModelItemCompleted(item)
                    yield ModelCompleted((item,))
                else:
                    result = next(x for x in request.items if isinstance(x, ToolResultItem))
                    assert not result.is_error, result.content
                    assert '"rejected","fulfilled"' in result.content
                    assert "sibling completed" in result.content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("parallel invalid result")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == len(calls) == 2
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                rows = db.execute(
                    "SELECT status,result_json FROM tool_executions WHERE tool_name='probe'"
                ).fetchall()
            assert len(rows) == 2 and all(row[0] == "completed" for row in rows)
            results = {json.loads(row[1])["call_id"]: json.loads(row[1]) for row in rows}
            for call in calls:
                result = results[call.id]
                assert result["tool_name"] == call.name
                assert result["is_error"] is (not call.arguments["valid"])
                assert result.get("dispatch_error", False) is (not call.arguments["valid"])
                assert result.get("mcp_result_json") is None
                if not call.arguments["valid"]:
                    expected = {
                        "call_id": "a result for a different call",
                        "tool_name": "a result for a different call",
                        "nan": "Invalid MCP host-event result JSON",
                        "infinity": "Invalid MCP host-event result JSON",
                        "dict": "dict instead of ToolResult",
                        "none": "NoneType instead of ToolResult",
                    }[flavor]
                    assert expected in result["content"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "flavor",
    [
        "fatal",
        "exception",
        "timeout",
        "invalid_result",
        "error_value",
        "schema_required",
        "schema_nested",
    ],
)
@pytest.mark.parametrize("caught", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("replay", [False, True])
def test_nested_error_channels_reach_js_and_durable_ledger(
    tmp_path, monkeypatch, flavor, caught, streamed, replay
):
    async def scenario():
        requests, calls = [], []
        if replay:
            identity = new_tool_call_id()
            monkeypatch.setattr("corki.code_mode.service.new_tool_call_id", lambda: identity)

        class Probe:
            spec = ToolSpec(
                "probe",
                "error channels",
                {"type": "object", "required": ["required_value"]}
                if flavor == "schema_required"
                else {
                    "type": "object",
                    "properties": {
                        "batch": {"type": "array", "items": {"type": "integer"}, "maxItems": 2}
                    },
                }
                if flavor == "schema_nested"
                else {"type": "object"},
            )

            async def execute(self, call, context):
                calls.append(call)
                if flavor == "fatal":
                    raise FatalToolError("fatal fixture")
                if flavor == "exception":
                    raise ValueError("exception fixture")
                if flavor == "timeout":
                    raise TimeoutError("timeout fixture")
                if flavor == "invalid_result":
                    return ToolResult(
                        call.id,
                        call.name,
                        "invalid fixture",
                        code_mode_output=CodeModeOutput(float("nan")),
                    )
                return ToolResult(
                    call.id,
                    call.name,
                    "error value fixture",
                    is_error=True,
                    code_mode_output=CodeModeOutput({"isError": True}),
                )

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    source = "const result = await tools.probe({}); text(['resolved',result]);"
                    if flavor == "schema_nested":
                        source = (
                            "const result = await tools.probe({batch:[true]});"
                            "text(['resolved',result]);"
                        )
                    if caught:
                        source = "try {" + source + "} catch(e) {text('caught:'+e);}"
                    source += "text('after-call');"
                    if replay:
                        source = "for(let i=0;i<2;i++){" + source + "}"
                    item = ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments=source,
                            input_kind="freeform",
                        ),
                        request.items[-1].turn_id,
                        new_step_id(),
                    )
                    if streamed:
                        yield ModelItemCompleted(item)
                    yield ModelCompleted((item,))
                else:
                    result = next(
                        item for item in request.items if isinstance(item, ToolResultItem)
                    )
                    dispatch_error = flavor != "error_value"
                    assert result.is_error == (dispatch_error and not caught), result.content
                    assert ("caught:" in result.content) == (dispatch_error and caught), (
                        result.content
                    )
                    assert ("resolved" in result.content) == (not dispatch_error), result.content
                    assert ("after-call" in result.content) == (caught or not dispatch_error)
                    if not dispatch_error:
                        assert '"isError":true' in result.content
                    else:
                        assert "Script error:" in result.content or "caught:" in result.content
                        expected = (
                            "missing required fields: required_value"
                            if flavor == "schema_required"
                            else "arguments.batch[0] must be integer"
                            if flavor == "schema_nested"
                            else "JSON compliant"
                            if flavor == "invalid_result"
                            else flavor + " fixture"
                        )
                        assert expected in result.content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("error channels")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            assert len(calls) == (0 if flavor.startswith("schema_") else 1)
            nested = [
                event
                for event in events
                if isinstance(event, ToolCallCompleted) and event.tool_name == "probe"
            ]
            expected_calls = 2 if replay and (caught or flavor == "error_value") else 1
            assert len(nested) == expected_calls and all(event.is_error for event in nested)
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                rows = db.execute(
                    "SELECT status,result_json FROM tool_executions WHERE tool_name='probe'"
                ).fetchall()
            assert len(rows) == 1 and rows[0][0] == "completed"
            payload = json.loads(rows[0][1])
            assert payload["is_error"] is True
            assert payload.get("dispatch_error", False) == (flavor != "error_value")
            assert "cancelled before dispatch" not in payload["content"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
def test_broken_nested_commit_cannot_be_hidden_by_js_catch(
    tmp_path, monkeypatch, committed, streamed
):
    async def scenario():
        requests, calls = [], []

        class Probe:
            spec = ToolSpec("probe", "side effect fixture", {})

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "effect completed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) == 1, "storage failure must not become model continuation"
                item = ToolCallItem(
                    ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=(
                            "try {await tools.probe({});} catch(e) {text(e);} text('after');"
                        ),
                    ),
                    request.items[-1].turn_id,
                    new_step_id(),
                )
                if streamed:
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        complete = runtime._repository.complete_tool_call

        async def broken_commit(thread, turn, result):
            if result.tool_name != "probe":
                return await complete(thread, turn, result)
            if committed:
                await complete(thread, turn, result)
            raise OSError("ledger commit fixture")

        monkeypatch.setattr(runtime._repository, "complete_tool_call", broken_commit)
        try:
            events = [event async for event in runtime.stream("write")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert "ledger commit fixture" in events[-1].error
            assert len(requests) == len(calls) == 1
            assert not runtime._code_mode.cells
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                status, payload = db.execute(
                    "SELECT status,result_json FROM tool_executions WHERE tool_name='probe'"
                ).fetchone()
            if committed:
                assert status == "completed"
                assert json.loads(payload)["content"] == "effect completed"
            else:
                assert status in {"running", "interrupted"} and payload is None
        finally:
            await runtime.aclose()

        cold_requests = []

        class ColdModel:
            async def stream(self, request):
                cold_requests.append(request)
                assert len(calls) == 1, "cold history repeated the side effect"
                yield ModelCompleted(())

            async def aclose(self):
                pass

        cold_registry = ToolRegistry()
        cold_registry.register(Probe())
        cold = await LangGraphRuntime.acreate(
            settings=runtime._settings,
            database_path=tmp_path / "sessions.db",
            registry=cold_registry,
            model=ColdModel(),
            thread_id=runtime.thread_id,
        )
        try:
            # A persisted terminal failure is not an unfinished Turn, even when
            # its graph checkpoint still contains the failed tool node.
            assert [event async for event in cold.resume_pending()] == []
            assert cold_requests == [] and len(calls) == 1
            # Reopen the actual failed transaction's identity, not a fresh call ID.
            cached = await cold._repository.claim_tool_call(
                cold.thread_id, events[-1].turn_id, calls[0]
            )
            assert cached is not None
            assert cached.is_error == (not committed)
            assert cached.dispatch_error == (not committed)
            if committed:
                assert cached.content == "effect completed"
            else:
                assert "outcome is unknown and was not repeated" in cached.content
            cold_events = [event async for event in cold.stream("inspect after failure")]
            assert isinstance(cold_events[-1], TurnCompleted)
            assert len(cold_requests) == 1
            assert len(calls) == 1
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                assert db.execute(
                    "SELECT status,result_json FROM tool_executions WHERE tool_name='probe'"
                ).fetchall() == [(status, payload)]
        finally:
            await cold.aclose()

    asyncio.run(scenario())
