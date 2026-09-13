"""A confirmed write retains the task outcome and owned final diagnostics."""

import asyncio
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed, WarningEvent
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("model_fails", [False, True])
@pytest.mark.parametrize("cancel_observer", [False, True])
@pytest.mark.parametrize("shutdown", [False, True])
def test_confirmed_terminal_preserves_original_outcome_during_observer_cancel(
    tmp_path, model_fails, cancel_observer, shutdown
):
    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        requests, events = [], []
        lifecycle = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if model_fails:
                    raise ModelError(
                        "original authentication failure", kind=ModelErrorKind.AUTHENTICATION
                    )
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                lifecycle.append("model closed")

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "terminal.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        save = runtime._repository.save_turn
        confirm = runtime._repository._confirm_turn_terminal
        close_storage = runtime._repository.close

        async def write_then_error(record):
            await save(record)
            if record.status.value != "running":
                raise OSError("terminal acknowledgment lost")

        def gated_confirmation(record):
            # Block inside the actual to_thread worker, not an async wrapper.
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(3):
                raise TimeoutError("fixture confirmation worker was not released")
            confirmed = confirm(record)
            lifecycle.append("confirmed")
            return confirmed

        async def observed_storage_close():
            await close_storage()
            lifecycle.append("storage closed")

        runtime._repository.save_turn = write_then_error
        runtime._repository._confirm_turn_terminal = gated_confirmation
        runtime._repository.close = observed_storage_close

        async def consume():
            async for event in runtime.stream("input"):
                events.append(event)

        observer = asyncio.create_task(consume())
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            run = runtime._active_run
            assert run.finishing
            if shutdown:
                closer = asyncio.create_task(runtime.aclose())
                await asyncio.sleep(0)
                assert not closer.done()
                assert lifecycle == []
            if cancel_observer:
                observer.cancel()
                await asyncio.sleep(0)
                assert not observer.done()
            release.set()
            result = (await asyncio.wait_for(asyncio.gather(observer, return_exceptions=True), 3))[
                0
            ]
            if cancel_observer:
                assert isinstance(result, asyncio.CancelledError)
            else:
                assert result is None
            assert run.done.is_set() and run.error is None and run.cleanup_error is None
            assert len(requests) == 1
            terminals = [
                event for event in events if isinstance(event, (TurnCompleted, TurnFailed))
            ]
            assert len(terminals) == 1
            terminal = terminals[0]
            if model_fails:
                assert isinstance(terminal, TurnFailed)
                assert terminal.error_kind == ModelErrorKind.AUTHENTICATION.value
                assert terminal.error == "original authentication failure"
            else:
                assert isinstance(terminal, TurnCompleted) and terminal.final_answer == "done"
            warnings = [
                event
                for event in events
                if isinstance(event, WarningEvent)
                and "terminal acknowledgment lost" in event.message
            ]
            assert len(warnings) == 1 and events.index(warnings[0]) < events.index(terminal)
            if closer is not None:
                await asyncio.wait_for(closer, 3)
                assert lifecycle == ["confirmed", "model closed", "storage closed"]
                await runtime.aclose()
                assert lifecycle == ["confirmed", "model closed", "storage closed"]
            else:
                assert [event async for event in runtime.resume_pending()] == []
        finally:
            release.set()
            await asyncio.gather(observer, return_exceptions=True)
            if closer is not None:
                await asyncio.gather(closer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_cancelled_terminal_ack_failure_is_confirmed_without_cleanup_error(tmp_path):
    async def scenario():
        started = asyncio.Event()
        requests, events = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                started.set()
                await asyncio.Event().wait()
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "cancelled.db",
            home_path=tmp_path / "home",
            model=Model(),
            registry=ToolRegistry(),
        )
        save = runtime._repository.save_turn

        async def write_then_error(record):
            await save(record)
            if record.status.value == "cancelled":
                raise OSError("cancelled terminal acknowledgment lost")

        runtime._repository.save_turn = write_then_error

        async def consume():
            async for event in runtime.stream("input"):
                events.append(event)

        observer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 3)
            run = runtime._active_run
            await runtime.cancel_active()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(observer, 3)
            terminals = [
                e for e in events if isinstance(e, (TurnCancelled, TurnCompleted, TurnFailed))
            ]
            assert len(terminals) == 1 and isinstance(terminals[0], TurnCancelled)
            assert run.cleanup_error is None
            warnings = [
                e
                for e in events
                if isinstance(e, WarningEvent)
                and "cancelled terminal acknowledgment lost" in e.message
            ]
            assert len(warnings) == 1 and events.index(warnings[0]) < events.index(terminals[0])
            assert len(requests) == 1 and not runtime._pending_terminals
            assert [e async for e in runtime.resume_pending()] == []
            assert len(requests) == 1
        finally:
            if not observer.done():
                observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)
            runtime._repository.save_turn = save
            await runtime.aclose()

    asyncio.run(scenario())
