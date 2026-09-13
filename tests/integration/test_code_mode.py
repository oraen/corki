"""Real ES modules, engine processes and nested Runtime tool execution."""

import asyncio
import json
import re
import sqlite3

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import ToolOutputDelta, TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolExposure, ToolResult, ToolSpec
from corki.tools import FatalToolError, ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


def request_call(request, name, value):
    call = ToolCall(
        new_tool_call_id(),
        name,
        None if name == "exec" else value,
        raw_arguments=value if name == "exec" else json.dumps(value),
        input_kind="freeform" if name == "exec" else "json",
    )
    return ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))


@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
@pytest.mark.parametrize("namespace_context", [False, True])
@pytest.mark.parametrize(
    "exposure", [ToolExposure.DIRECT, ToolExposure.DEFERRED, ToolExposure.CODE_MODE_ONLY]
)
def test_real_cell_discovers_and_invokes_normal_ledger_backed_tool(
    tmp_path, mode, exposure, namespace_context
):
    async def scenario():
        calls, requests = [], []
        name = "vault::probe" if namespace_context else "probe"
        alias = "vault__probe" if namespace_context else "probe"

        class Probe:
            spec = ToolSpec(
                name,
                "multiply a number",
                {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
                exposure=exposure,
                namespace_description="private calculations" if namespace_context else None,
            )

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, str(call.arguments["n"] * 2))

        class Model:
            async def stream(self, request):
                requests.append(request)
                catalogs = [
                    i
                    for i in request.items
                    if isinstance(i, ContextItem) and i.key == "tools.deferred_namespaces"
                ]
                assert len(catalogs) == int(namespace_context and exposure == ToolExposure.DEFERRED)
                if catalogs:
                    assert "- vault: private calculations" in catalogs[0].content
                if len(requests) == 1:
                    assert "exec" in {spec.name for spec in request.tools}
                    assert (name in {spec.name for spec in request.tools}) == (
                        mode == "code_mode" and exposure == ToolExposure.DIRECT
                    )
                    yield request_call(
                        request,
                        "exec",
                        f"const found = ALL_TOOLS.find(t => t.name === '{alias}'); "
                        "const r = await tools[found.name]({n:21}); text(r);",
                    )
                else:
                    result = next(
                        item
                        for item in request.items
                        if isinstance(item, ToolResultItem) and item.tool_name == "exec"
                    )
                    assert "Script completed" in result.content and "42" in result.content, (
                        result.content
                    )
                    assert not any(
                        isinstance(item, ToolCallItem) and item.call.name == name
                        for item in request.items
                    )
                    yield ModelCompleted(
                        (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                    )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                deferred_tool_world_state=namespace_context,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run module")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(calls) == 1 and len(requests) == 2
            with sqlite3.connect(tmp_path / "sessions.db") as connection:
                assert connection.execute(
                    "SELECT tool_name,status FROM tool_executions ORDER BY tool_name"
                ).fetchall() == [("exec", "completed"), (name, "completed")]
            assert not runtime._code_mode.cells
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_yield_wait_incremental_output_and_session_store(tmp_path):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec",
                        "text('first'); yield_control(); await new Promise(r=>setTimeout(r,100)); "
                        "store('value',{n:42}); text('second');",
                    )
                elif len(requests) == 2:
                    emitted = [part.text for part in results[-1].content_items[1:]]
                    assert emitted == ["first"]
                    assert not await runtime._repository.code_mode_parent_finished(
                        runtime.thread_id, results[-1].call_id
                    )
                    cell_id = re.search(r"cell ID (\S+)", results[-1].content)[1]
                    yield request_call(request, "wait", {"cell_id": cell_id})
                elif len(requests) == 3:
                    assert "second" in results[-1].content and "first" not in results[-1].content
                    assert await runtime._repository.code_mode_parent_finished(
                        runtime.thread_id, results[-2].call_id
                    )
                    yield request_call(request, "exec", "text(load('value')); text(typeof value);")
                else:
                    assert '{"n":42}' in results[-1].content and "undefined" in results[-1].content
                    yield ModelCompleted(
                        (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                    )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 4
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "text([typeof process,typeof require,typeof fetch,typeof console,"
            "typeof Atomics,typeof WebAssembly]);",
            "undefined",
        ),
        ("import x from 'node:fs'; text(x);", "Script failed"),
        ("await import('std');", "Script failed"),
        ("throw new Error('fixture error');", "fixture error"),
        ("exit(); text('MUST_NOT_RUN');", "Script completed"),
        ("setTimeout(()=>text('MUST_NOT_RUN'),10); text('ended');", "ended"),
    ],
)
def test_engine_module_boundaries_are_model_observations(tmp_path, source, expected):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(request, "exec", source)
                else:
                    result = next(
                        item for item in request.items if isinstance(item, ToolResultItem)
                    )
                    assert expected in result.content and "MUST_NOT_RUN" not in result.content, (
                        result.content
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("busy", [False, True])
def test_cancel_joins_busy_engine_and_nested_tool(tmp_path, busy):
    async def scenario():
        started, closed = asyncio.Event(), asyncio.Event()
        requests, events = [], []

        class Probe:
            spec = ToolSpec("hang", "fixture", {"type": "object"})

            async def execute(self, call, context):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    source = "notify('busy'); while(true){}" if busy else "await tools.hang({});"
                    yield request_call(request, "exec", '// @exec: {"yield_time_ms":0}\n' + source)
                else:
                    await asyncio.Event().wait()

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )

        async def consume():
            try:
                async for event in runtime.stream("run"):
                    events.append(event)
                    if isinstance(event, ToolOutputDelta) and event.delta == "busy":
                        started.set()
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 3)
            cells = tuple(runtime._code_mode.cells.values())
            assert cells
            await runtime.cancel_active()
            await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCancelled), events[-1]
            assert closed.is_set() or busy
            assert all(cell.task.done() and cell.process.returncode is not None for cell in cells)
            assert not runtime._code_mode.cells
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("fatal", [False, True])
def test_nested_errors_use_normal_tool_boundary(tmp_path, fatal):
    async def scenario():
        requests = []

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                if fatal:
                    raise FatalToolError("fatal fixture")
                raise ValueError("observation fixture")

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec",
                        "try { text(await tools.probe({})); } catch(e) { text(e); }",
                    )
                else:
                    result = next(
                        item for item in request.items if isinstance(item, ToolResultItem)
                    )
                    assert ("fatal fixture" if fatal else "observation fixture") in result.content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_nested_parallel_exclusive_order_and_unawaited_tool_cleanup(tmp_path):
    async def scenario():
        starts, ends = [], []
        both = asyncio.Event()
        hanging, hang_closed = asyncio.Event(), asyncio.Event()

        class Probe:
            def __init__(self, name, parallel):
                self.spec = ToolSpec(
                    name,
                    "fixture",
                    {"type": "object"},
                    concurrency=ToolConcurrency.PARALLEL if parallel else ToolConcurrency.EXCLUSIVE,
                )

            async def execute(self, call, context):
                name = call.arguments.get("name", call.name)
                starts.append(name)
                if name in {"a", "b"}:
                    if all(value in starts for value in ("a", "b")):
                        both.set()
                    await asyncio.wait_for(both.wait(), 1)
                elif name == "exclusive":
                    assert ends == ["b", "a"] or ends == ["a", "b"]
                elif name == "hang":
                    hanging.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        hang_closed.set()
                elif name == "ready":
                    await hanging.wait()
                ends.append(name)
                return ToolResult(call.id, call.name, name)

        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec",
                        "await Promise.all([tools.parallel({name:'a'}),tools.parallel({name:'b'}),"
                        "tools.exclusive({})]); tools.parallel({name:'hang'}); "
                        "await tools.parallel({name:'ready'}); text('finished');",
                    )
                else:
                    assert hang_closed.is_set()
                    result = next(
                        item for item in request.items if isinstance(item, ToolResultItem)
                    )
                    assert "Script completed" in result.content, result.content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe("parallel", True))
        registry.register(Probe("exclusive", False))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert set(starts[:2]) == {"a", "b"} and starts[2] == "exclusive"
            assert "hang" not in ends and hang_closed.is_set()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
