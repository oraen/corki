"""Cells across steps/turns, restart boundaries and actual Responses requests."""

import asyncio
import json
import os
import re
import signal
import sys
from contextlib import suppress
from pathlib import Path

import httpx
import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind, ModelItemCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import CodeModeOutput, ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


def invoke(request, name, value):
    return ModelCompleted(
        (
            ToolCallItem(
                ToolCall(
                    new_tool_call_id(),
                    name,
                    None if name == "exec" else value,
                    raw_arguments=value if name == "exec" else json.dumps(value),
                    input_kind="freeform" if name == "exec" else "json",
                ),
                request.items[-1].turn_id,
                new_step_id(),
            ),
        )
    )


@pytest.mark.parametrize("reopen", [False, True])
def test_yielded_cell_survives_turn_not_process_and_never_replays_side_effect(tmp_path, reopen):
    async def scenario():
        executed, release = asyncio.Event(), asyncio.Event()
        calls = []
        requests, cell_ids = [], []

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                executed.set()
                await release.wait()
                return ToolResult(call.id, call.name, "finished side effect")

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield invoke(
                        request,
                        "exec",
                        '// @exec: {"yield_time_ms":10}\n'
                        "await tools.probe({}); store('committed',42); text('background done');",
                    )
                elif len(requests) == 2:
                    await asyncio.wait_for(executed.wait(), 2)
                    result = next(
                        item for item in request.items if isinstance(item, ToolResultItem)
                    )
                    cell_ids.append(re.search(r"cell ID (\S+)", result.content)[1])
                    yield ModelCompleted(())
                elif len(requests) == 3:
                    release.set()
                    yield invoke(request, "wait", {"cell_id": cell_ids[0]})
                elif len(requests) == 4:
                    result = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    assert ("No live cell" if reopen else "background done") in result.content, (
                        result.content
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Probe())
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
                ),
                database_path=tmp_path / "sessions.db",
                registry=registry,
                model=Model(),
                thread_id=thread,
            )

        runtime = create()
        try:
            first = [event async for event in runtime.stream("start background")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            assert runtime._code_mode.cells
            if reopen:
                thread = runtime.thread_id
                await runtime.aclose()
                runtime = create(thread)
            second = [event async for event in runtime.stream("observe background")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert len(calls) == 1 and len(requests) == 4
            assert runtime._code_mode.stored == ({} if reopen else {"committed": 42})
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_responses_wire_to_real_module_to_nested_tool_and_back(
    tmp_path, monkeypatch, native, typed
):
    async def scenario():
        requests, calls = [], []

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(
                    call.id,
                    call.name,
                    "diagnostic only" if typed else "actual nested output",
                    code_mode_output=CodeModeOutput({"answer": "actual nested output"})
                    if typed
                    else None,
                )

        def handle(request):
            payload = json.loads(request.content)
            requests.append(payload)
            body = ""
            if len(requests) == 1:
                definition = next(tool for tool in payload["tools"] if tool.get("name") == "exec")
                assert definition["type"] == ("custom" if native else "function")
                source = (
                    "const r = await tools.probe({}); text(r.answer);"
                    if typed
                    else "text(await tools.probe({}));"
                )
                item = {
                    "type": "custom_tool_call" if native else "function_call",
                    "name": "exec",
                    "id": "i",
                    "call_id": "c",
                }
                item.update(
                    {"input": source} if native else {"arguments": json.dumps({"input": source})}
                )
                body = (
                    "data: "
                    + json.dumps({"type": "response.output_item.done", "item": item})
                    + "\n\n"
                )
            else:
                result = next(
                    item
                    for item in payload["input"]
                    if item.get("type")
                    == ("custom_tool_call_output" if native else "function_call_output")
                )
                # Code Mode now preserves its ordered output items on Responses.
                rendered = "\n".join(part["text"] for part in result["output"])
                assert "Script completed" in rendered and "actual nested output" in rendered
                assert "diagnostic only" not in rendered
            body += 'data: {"type":"response.completed","response":{"id":"r"}}\n\n'
            return httpx.Response(200, text=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        registry = ToolRegistry()
        registry.register(Probe())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode="code_mode_only",
                api_mode="responses",
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                tool_freeform_mode="native" if native else "compatible",
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and len(calls) == 1
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="POSIX liveness assertion")
def test_parent_process_death_reaps_even_synchronous_busy_cell(tmp_path):
    async def scenario():
        fixture = Path(__file__).parents[1] / "fixtures" / "code_mode_parent_exit.py"
        parent = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pid = None
        try:
            output, errors = await asyncio.wait_for(parent.communicate(), 8)
            assert parent.returncode == 23, errors.decode()
            pid = int(output.strip())

            async def gone():
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        return
                    await asyncio.sleep(0.02)

            await asyncio.wait_for(gone(), 3)
        finally:
            if parent.returncode is None:
                parent.kill()
                await parent.wait()
            if pid is not None:
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)

    asyncio.run(scenario())


def test_store_commits_js_errors_but_not_terminated_cell_and_plan_reaches_graph(tmp_path):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                if len(requests) == 1:
                    yield invoke(request, "exec", "store('kept',42); throw Error('fixture');")
                elif len(requests) == 2:
                    assert "Script failed" in results[-1].content
                    yield invoke(
                        request,
                        "exec",
                        "store('lost',99); yield_control(); await new Promise(()=>{});",
                    )
                elif len(requests) == 3:
                    cell_id = re.search(r"cell ID (\S+)", results[-1].content)[1]
                    yield invoke(request, "wait", {"cell_id": cell_id, "terminate": True})
                elif len(requests) == 4:
                    assert "Script terminated" in results[-1].content
                    yield invoke(
                        request,
                        "exec",
                        "text(load('kept')); text(load('lost')); "
                        "await tools.update_plan({plan:[{step:'verify',status:'completed'}]});",
                    )
                else:
                    assert "42" in results[-1].content and "undefined" in results[-1].content
                    assert results[-1].state_update.plan == (
                        {"step": "verify", "status": "completed"},
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
            assert len(requests) == 5
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("retry", [False, True])
def test_new_step_has_its_own_nested_execution_gate_but_retry_retains_it(tmp_path, retry):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        requests, cell_ids = [], []

        class Probe:
            def __init__(self, name):
                self.spec = ToolSpec(
                    name,
                    "fixture",
                    {"type": "object"},
                    concurrency=ToolConcurrency.PARALLEL
                    if name in {"hold", "ready"}
                    else ToolConcurrency.EXCLUSIVE,
                )

            async def execute(self, call, context):
                if call.name == "hold":
                    started.set()
                    await release.wait()
                elif call.name == "ready":
                    await started.wait()
                else:
                    release.set()
                return ToolResult(call.id, call.name, call.name)

        class Model:
            async def stream(self, request):
                requests.append(request)
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                if len(requests) == 1:
                    response = invoke(
                        request,
                        "exec",
                        "const held = tools.hold({}); await tools.ready({}); "
                        "yield_control(); text(await held);",
                    )
                    if retry:
                        yield ModelItemCompleted(response.items[0])
                        await asyncio.wait_for(started.wait(), 2)
                        raise ModelError(
                            "retry fixture", kind=ModelErrorKind.TRANSPORT, retryable=True
                        )
                    yield response
                elif len(requests) == 2:
                    await asyncio.wait_for(started.wait(), 2)
                    cell_ids.append(re.search(r"cell ID (\S+)", results[-1].content)[1])
                    if retry:
                        assert len(runtime._code_mode.step_calls) == 2
                        assert not runtime._code_mode.step_calls[0].done()
                        # Same-Step exclusive release would sit behind hold.
                        # Release externally only after proving the gate survived.
                        release.set()
                    else:
                        assert runtime._code_mode.step_calls == []
                    yield invoke(request, "exec", "text(await tools.release({}));")
                elif len(requests) == 3:
                    assert "Script completed" in results[-1].content
                    yield invoke(request, "wait", {"cell_id": cell_ids[0]})
                else:
                    assert "hold" in results[-1].content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe("hold"))
        registry.register(Probe("ready"))
        registry.register(Probe("release"))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )

        async def run():
            return [event async for event in runtime.stream("run")]

        try:
            events = await asyncio.wait_for(run(), 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 4
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
