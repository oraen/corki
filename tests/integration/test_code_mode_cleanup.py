"""Infrastructure cleanup faults cannot strand callbacks or erase a Turn terminal."""

import asyncio
from contextlib import suppress

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


@pytest.mark.parametrize("spawn_failure", [False, True])
def test_runtime_joins_cancelled_cell_spawn_before_continuing(tmp_path, monkeypatch, spawn_failure):
    async def scenario():
        spawned, release = asyncio.Event(), asyncio.Event()
        processes, requests = [], []
        original = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            if not any(
                str(arg).replace("\\", "/").endswith("/code_mode/worker.py") for arg in args
            ):
                return await original(*args, **kwargs)
            if spawn_failure:
                spawned.set()
                await release.wait()
                raise OSError("engine creation failed after cancellation")
            process = await original(*args, **kwargs)
            processes.append(process)
            spawned.set()
            await release.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    "exec",
                                    None,
                                    raw_arguments="store('unexpected', true);",
                                    input_kind="freeform",
                                ),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    assert all(process.returncode is not None for process in processes)
                    assert any(
                        "Script terminated" in getattr(i, "content", "") for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, tool_mode="code_mode_only"),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
            registry=ToolRegistry(),
            model=Model(),
        )

        async def consume():
            return [event async for event in runtime.stream("execute")]

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(spawned.wait(), 3)
            (cell,) = runtime._code_mode.cells.values()
            for _ in range(2):
                cell.task.cancel()
                await asyncio.sleep(0)
            release.set()
            events = await asyncio.wait_for(consumer, 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and len(processes) == (0 if spawn_failure else 1)
            if spawn_failure:
                assert cell.process is None and cell.task.cancelled()
            else:
                assert cell.process is processes[0] and cell.process.stdin.is_closing()
            assert not runtime._code_mode.cells and not runtime._code_mode.stored
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()
            for process in processes:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                process.stdin.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["wait", "pipe"])
@pytest.mark.parametrize("cancel", [False, True])
def test_real_cell_cleanup_fault_preserves_terminal_and_joins_callbacks(
    tmp_path, monkeypatch, boundary, cancel
):
    async def scenario():
        started, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sampling = asyncio.Event()
        requests, events, invocations = [], [], []
        stray_tasks = []

        class Hold:
            spec = ToolSpec("hold", "held side effect", {"type": "object"})

            async def execute(self, call, context):
                invocations.append(call.id)
                started.set()
                try:
                    await release.wait()
                    return ToolResult(call.id, call.name, "done")
                finally:
                    closed.set()

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    "exec",
                                    None,
                                    raw_arguments='// @exec: {"yield_time_ms":0}\n'
                                    "await tools.hold({}); text('done');",
                                    input_kind="freeform",
                                ),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                elif len(requests) == 2:
                    sampling.set()
                    await asyncio.Event().wait()
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Hold())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )

        async def consume():
            with suppress(asyncio.CancelledError):
                async for event in runtime.stream("first", realtime=True):
                    events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 3)
            await asyncio.wait_for(sampling.wait(), 3)
            (cell,) = runtime._code_mode.cells.values()
            original_wait, original_close = cell.process.wait, cell.process.stdin.close

            def fault():
                # Work admitted before final cleanup must also be joined after a process fault.
                notification = asyncio.create_task(asyncio.Event().wait())
                callback = asyncio.create_task(asyncio.Event().wait())
                cell.notifications.add(notification)
                cell.tool_tasks.add(callback)
                stray_tasks.extend((notification, callback))
                raise OSError(f"injected {boundary} cleanup fault")

            if boundary == "wait":

                async def failed_wait():
                    await original_wait()
                    fault()

                monkeypatch.setattr(cell.process, "wait", failed_wait)
            else:
                original_pipe = cell.process.stdin

                class FailedPipe:
                    def __getattr__(self, name):
                        return getattr(original_pipe, name)

                    def close(self):
                        original_close()
                        fault()

                # Do not replace asyncio's own protocol callback's writer.
                monkeypatch.setattr(cell.process, "stdin", FailedPipe())

            if cancel:
                await runtime.cancel_active()
            else:
                release.set()
            await asyncio.wait_for(asyncio.shield(consumer), 3)
            terminal = events[-1]
            assert isinstance(terminal, TurnCancelled if cancel else TurnFailed), terminal
            assert (
                sum(isinstance(e, (TurnCompleted, TurnFailed, TurnCancelled)) for e in events) == 1
            )
            assert closed.is_set() and cell.task.done()
            assert cell.process.returncode is not None and cell.process.stdin.is_closing()
            assert cell.ready.is_set() and cell.changed.is_set()
            assert stray_tasks and all(task.done() for task in stray_tasks)
            assert not runtime._realtime.active
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            followup = [event async for event in runtime.stream("next")]
            assert isinstance(followup[-1], TurnCompleted)
            assert len(invocations) == 1
            with pytest.raises(OSError, match=f"injected {boundary} cleanup fault"):
                await runtime.aclose()
        finally:
            release.set()
            consumer.cancel()
            for task in stray_tasks:
                task.cancel()
            await asyncio.gather(consumer, *stray_tasks, return_exceptions=True)
            with suppress(OSError):
                await runtime.aclose()

    asyncio.run(scenario())


def test_cancel_joins_all_cells_and_calls_even_when_one_termination_fails(tmp_path):
    async def scenario():
        sampling, terminating, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events, joined = [], []

        class Model:
            async def stream(self, request):
                sampling.set()
                await asyncio.Event().wait()
                yield ModelCompleted(())

            async def aclose(self):
                joined.append("model")

        class BrokenCell:
            async def terminate(self):
                raise OSError("cell termination fault")

        class HeldCell:
            async def terminate(self):
                terminating.set()
                await release.wait()
                joined.append("cell")

        async def held_call():
            try:
                await asyncio.Event().wait()
            finally:
                joined.append("call")

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )

        async def consume():
            with suppress(asyncio.CancelledError):
                async for event in runtime.stream("cancel", realtime=True):
                    events.append(event)

        consumer = asyncio.create_task(consume())
        call = None
        try:
            await asyncio.wait_for(sampling.wait(), 3)
            runtime._code_mode.cells.update(broken=BrokenCell(), held=HeldCell())
            call = asyncio.create_task(held_call())
            runtime._code_mode.calls.append(call)
            await asyncio.sleep(0)
            await runtime.cancel_active()
            await asyncio.wait_for(terminating.wait(), 3)
            await asyncio.sleep(0)
            assert not runtime._active_run.done.is_set()
            release.set()
            await asyncio.wait_for(asyncio.shield(consumer), 3)
            assert isinstance(events[-1], TurnCancelled)
            assert joined == ["cell", "call"]
            assert not runtime._code_mode.cells and not runtime._realtime.active
            with pytest.raises(OSError, match="cell termination fault"):
                await runtime.aclose()
            assert joined == ["cell", "call", "model"]
        finally:
            release.set()
            consumer.cancel()
            if call is not None:
                call.cancel()
            await asyncio.gather(
                consumer, *([call] if call is not None else []), return_exceptions=True
            )
            with suppress(OSError):
                await runtime.aclose()

    asyncio.run(scenario())
