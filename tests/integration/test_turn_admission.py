import asyncio
from dataclasses import replace

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed, WarningEvent
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("old_paused", ["started", "terminal"])
def test_new_turn_admission_does_not_require_old_observer_to_close(tmp_path, old_paused):
    async def scenario():
        entered, new_entered, finish_new = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        class Model:
            async def stream(self, request):
                calls.append(request)
                if len(calls) == 1:
                    entered.set()
                    if old_paused == "started":
                        await asyncio.Event().wait()
                else:
                    new_entered.set()
                    await finish_new.wait()
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, event_queue_size=1
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        old = runtime.stream("old", realtime=True)
        new = runtime.compact() if old_paused == "started" else runtime.stream("new", realtime=True)
        third = runtime.stream("third")
        admission = third_admission = None
        try:
            await anext(old)
            await asyncio.wait_for(entered.wait(), 3)
            old_run = runtime._active_run
            if old_paused == "terminal":
                while not isinstance(await anext(old), TurnCompleted):
                    pass
            admission = asyncio.create_task(anext(new))
            await asyncio.wait_for(old_run.done.wait(), 3)
            assert isinstance(
                old_run.result(), TurnCancelled if old_paused == "started" else TurnCompleted
            )
            await asyncio.wait_for(asyncio.shield(admission), 0.5)
            await asyncio.wait_for(new_entered.wait(), 3)
            new_run = runtime._active_run
            assert new_run is not old_run
            third_admission = asyncio.create_task(anext(third))
            await asyncio.sleep(0)
            assert runtime._turn_lock.locked() and not third_admission.done()
            await old.aclose()
            assert runtime._active_run is new_run and not new_run.cancel_requested
            assert runtime._realtime.active is (old_paused == "terminal")
            assert not third_admission.done() and len(calls) == 2
            finish_new.set()
            events = [e async for e in new]
            assert isinstance(events[-1], TurnCompleted)
            await asyncio.wait_for(third_admission, 3)
            assert isinstance([e async for e in third][-1], TurnCompleted)
            assert len(calls) == 3
        finally:
            for pending in (admission, third_admission):
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            finish_new.set()
            await old.aclose()
            await new.aclose()
            await third.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["terminal-write", "cleanup"])
def test_admission_waits_for_owned_persistence_and_cleanup(tmp_path, boundary):
    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Model:
            async def stream(self, request):
                calls.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        await runtime._ensure_ready()
        save = runtime._repository.save_turn

        async def save_blocked(record):
            if record.status.value == "completed" and len(calls) == 1:
                reached.set()
                await release.wait()
            await save(record)

        class Cleanup(CodeModeService):
            async def deactivate(self, **kwargs):
                if len(calls) == 1:
                    reached.set()
                    await release.wait()
                await super().deactivate(**kwargs)

        if boundary == "terminal-write":
            runtime._repository.save_turn = save_blocked
        else:
            # Exercise the Runtime's actual final cleanup await, not a model delay.
            runtime._code_mode = Cleanup(runtime._registry)
        old, new = runtime.stream("old"), runtime.stream("new")
        admission = None
        try:
            await anext(old)
            await asyncio.wait_for(reached.wait(), 3)
            run = runtime._active_run
            admission = asyncio.create_task(anext(new))
            await asyncio.sleep(0)
            assert not run.done.is_set() and not admission.done() and len(calls) == 1
            release.set()
            await asyncio.wait_for(admission, 3)
            assert run.done.is_set()
            assert isinstance([e async for e in new][-1], TurnCompleted)
            assert len(calls) == 2
        finally:
            release.set()
            if admission is not None and not admission.done():
                admission.cancel()
                await asyncio.gather(admission, return_exceptions=True)
            await old.aclose()
            await new.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


def test_queued_compact_replaces_the_worker_that_started_while_it_waited(tmp_path):
    async def scenario():
        entered, compact_entered, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Model:
            async def stream(self, request):
                if "checkpoint compaction" in getattr(request.items[-1], "content", ""):
                    compact_entered.set()
                    await finish.wait()
                    yield ModelCompleted(
                        (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                    )
                else:
                    entered.set()
                    await asyncio.Event().wait()

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, event_queue_size=1
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        old, middle, new = runtime.stream("old"), runtime.stream("queued"), runtime.compact()
        tasks = []
        try:
            await anext(old)
            await asyncio.wait_for(entered.wait(), 3)
            tasks.append(asyncio.create_task(anext(middle)))
            await asyncio.sleep(0)
            assert runtime._turn_lock.locked() and not tasks[0].done()
            tasks.append(asyncio.create_task(anext(new)))
            await asyncio.wait_for(asyncio.gather(*tasks), 3)
            await asyncio.wait_for(compact_entered.wait(), 3)
            current = runtime._active_run
            await old.aclose()
            await middle.aclose()
            assert runtime._active_run is current and not current.cancel_requested
            finish.set()
            assert isinstance([e async for e in new][-1], TurnCompleted)
        finally:
            finish.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await old.aclose()
            await middle.aclose()
            await new.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("write_failed", [False, "before", "after", "read_error", "conflict"])
def test_resume_does_not_wait_for_old_terminal_observation_or_resample(tmp_path, write_failed):
    async def scenario():
        calls = []

        class Model:
            async def stream(self, request):
                calls.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        save = runtime._repository.save_turn
        failed = False

        async def fail_once(record):
            nonlocal failed
            if write_failed and not failed and record.status.value == "completed":
                failed = True
                if write_failed in {"after", "read_error", "conflict"}:
                    await save(
                        replace(record, final_answer="DIFFERENT")
                        if write_failed == "conflict"
                        else record
                    )
                raise OSError("terminal write fixture")
            await save(record)

        runtime._repository.save_turn = fail_once
        if write_failed == "read_error":

            async def unavailable(record):
                raise OSError("confirmation unavailable")

            runtime._repository.confirm_turn_terminal = unavailable
        old = runtime.stream("old")
        try:
            await anext(old)
            await asyncio.wait_for(runtime._active_run.done.wait(), 3)
            terminal = runtime._active_run.terminal
            unconfirmed = write_failed == "conflict"
            assert isinstance(terminal, TurnFailed if unconfirmed else TurnCompleted)
            if unconfirmed:
                assert terminal.error_kind == "storage"
                assert "terminal write fixture" in terminal.error
            if write_failed == "after":
                assert runtime._active_run.cleanup_error is None
                warnings = [
                    event
                    for event in runtime._active_run.final_events
                    if isinstance(event, WarningEvent)
                ]
                assert len(warnings) == 1 and "terminal write fixture" in warnings[0].message

            async def resume():
                return [e async for e in runtime.resume_pending()]

            events = await asyncio.wait_for(resume(), 3)
            # Owned pending terminals drain before inspecting the recovery ledger;
            # even a failed initial write no longer needs another graph execution.
            assert events == []
            assert len(calls) == 1
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            remaining = [event async for event in old]
            if write_failed == "after":
                assert isinstance(remaining[-1], TurnCompleted)
                assert (
                    sum(
                        isinstance(event, WarningEvent)
                        and "terminal write fixture" in event.message
                        for event in remaining
                    )
                    == 1
                )
                assert not any(isinstance(event, TurnFailed) for event in remaining)
        finally:
            await old.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("close_runtime", [False, True])
def test_cancelled_admission_does_not_cancel_the_previous_worker(tmp_path, close_runtime):
    async def scenario():
        entered = asyncio.Event()

        class Model:
            async def stream(self, request):
                entered.set()
                await asyncio.Event().wait()
                yield

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        old, new = runtime.stream("old"), runtime.stream("queued")
        admission = None
        try:
            await anext(old)
            await asyncio.wait_for(entered.wait(), 3)
            previous = runtime._active_run
            admission = asyncio.create_task(anext(new))
            await asyncio.sleep(0)
            assert not admission.done()
            if close_runtime:
                await asyncio.wait_for(runtime.aclose(), 3)
                with pytest.raises(RuntimeError, match="closed"):
                    await asyncio.wait_for(admission, 3)
                assert previous.done.is_set()
            else:
                admission.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await admission
                assert not previous.cancel_requested and not previous.done.is_set()
                assert not runtime._turn_lock.locked()
        finally:
            if admission is not None and not admission.done():
                admission.cancel()
                await asyncio.gather(admission, return_exceptions=True)
            await old.aclose()
            await new.aclose()
            await runtime.aclose()

    asyncio.run(scenario())
