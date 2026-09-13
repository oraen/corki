"""An independently cancelled dispatch must settle its live JavaScript promise."""

import asyncio
import sqlite3

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolSpec
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


@pytest.mark.parametrize("self_cancel", [False, True])
@pytest.mark.parametrize("caught", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
def test_independent_nested_cancellation_settles_promise(tmp_path, self_cancel, caught, streamed):
    async def scenario():
        requests, calls = [], []
        finalized = asyncio.Event()

        class Probe:
            spec = ToolSpec("probe", "cancel during execution", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                try:
                    if self_cancel:
                        asyncio.current_task().cancel()
                        await asyncio.sleep(0)
                    raise asyncio.CancelledError("independent handler cancellation")
                finally:
                    finalized.set()

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    source = "await tools.probe({}); text('unexpected resolution');"
                    if caught:
                        source = "try {" + source + "} catch(e) {text('caught:'+e);}"
                    source += "text('after-call');"
                    item = ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments='// @exec: {"yield_time_ms":1000}\n' + source,
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
                    assert "Script running" not in result.content, result.content
                    assert "code mode nested tool call cancelled" in result.content
                    assert result.is_error == (not caught)
                    assert ("caught:" in result.content) == caught
                    assert ("after-call" in result.content) == caught
                    assert "unexpected resolution" not in result.content
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
            async with asyncio.timeout(10):
                events = [event async for event in runtime.stream("cancel one nested call")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert (
                sum(
                    isinstance(event, (TurnCancelled, TurnCompleted, TurnFailed))
                    for event in events
                )
                == 1
            )
            assert len(requests) == 2 and len(calls) == 1 and finalized.is_set()
            assert not runtime._code_mode.cells
            assert all(task.done() for task in runtime._code_mode.calls)
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                rows = db.execute(
                    "SELECT status,result_json FROM tool_executions WHERE tool_name='probe'"
                ).fetchall()
            # The handler ran: cancellation cannot assert a successful/no-effect outcome.
            # The current ledger represents unknown outcomes as unresolved claims,
            # not a separate interrupted status. Reclaim must refuse execution.
            assert rows == [("running", None)]
            cached = await runtime._repository.claim_tool_call(
                runtime.thread_id, events[-1].turn_id, calls[0]
            )
            assert cached.is_error and cached.dispatch_error
            assert "outcome is unknown and was not repeated" in cached.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
def test_turn_cancellation_is_not_a_catchable_nested_failure(tmp_path, streamed):
    async def scenario():
        started, finalized = asyncio.Event(), asyncio.Event()
        requests, events = [], []

        class Probe:
            spec = ToolSpec("probe", "held nested call", {"type": "object"})

            async def execute(self, call, context):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    finalized.set()

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) == 1, "Turn cancellation cannot resume sampling"
                item = ToolCallItem(
                    ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=(
                            "try {await tools.probe({});} catch(e) {text('caught:'+e);} "
                            "store('continued',true); text('unexpected continuation');"
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
                async for event in runtime.stream("cancel the whole Turn"):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(5):
                await started.wait()
                cells = tuple(runtime._code_mode.cells.values())
                await runtime.cancel_active()
                await consumer
            assert isinstance(events[-1], TurnCancelled), events[-1]
            assert (
                sum(
                    isinstance(event, (TurnCancelled, TurnCompleted, TurnFailed))
                    for event in events
                )
                == 1
            )
            assert finalized.is_set() and len(requests) == 1
            assert cells and all(
                cell.task.done() and cell.process.returncode is not None for cell in cells
            )
            assert not runtime._code_mode.cells
            assert all(task.done() for task in runtime._code_mode.calls)
            assert "continued" not in runtime._code_mode.stored
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
