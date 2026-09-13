"""Cold recovery reconciles a task whose terminal row never committed."""

import asyncio
import sqlite3
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("stored", ["running", "same", "conflict", "unadmitted"])
@pytest.mark.parametrize("status_read_fails", [False, True])
def test_failed_terminal_reads_keep_owned_result_until_verified(
    tmp_path, stored, status_read_fails
):
    from dataclasses import replace

    from corki.storage.sqlite import StorageIntegrityError

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "reads.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        repository = runtime._repository
        save = repository.save_turn
        confirm, status = repository.confirm_turn_terminal, repository.load_turn_status
        retry = repository.retry_turn_terminal

        async def unavailable(*args):
            raise OSError("read unavailable")

        async def ambiguous_write(record):
            if record.status is TurnStatus.RUNNING and stored != "unadmitted":
                await save(record)
                return
            if stored not in {"running", "unadmitted"}:
                await save(
                    replace(record, final_answer="OTHER") if stored == "conflict" else record
                )
            repository.confirm_turn_terminal = unavailable
            if status_read_fails:
                repository.load_turn_status = unavailable
            raise OSError("write acknowledgment unavailable")

        repository.save_turn = ambiguous_write
        repository.retry_turn_terminal = unavailable
        pending = None
        try:
            events = [event async for event in runtime.stream("input")]
            if stored == "unadmitted":
                assert isinstance(events[-1], TurnFailed) and events[-1].error_kind == "storage"
                assert not runtime._pending_terminals and not requests
                assert await status(runtime.thread_id, events[-1].turn_id) is None
                return
            assert isinstance(events[-1], TurnCompleted)
            assert len(runtime._pending_terminals) == 1
            pending = next(iter(runtime._pending_terminals.values()))
            assert pending.final_answer == "done"
            repository.confirm_turn_terminal, repository.load_turn_status = confirm, status
            repository.retry_turn_terminal = retry
            if stored == "conflict":
                with pytest.raises(StorageIntegrityError, match="conflicts"):
                    _ = [event async for event in runtime.resume_pending()]
                assert await confirm(replace(pending, final_answer="OTHER"))
                assert runtime._pending_terminals
                await save(pending)  # Resolve only the fixture's injected conflict.
            assert [event async for event in runtime.resume_pending()] == []
            assert await confirm(pending)
            assert not runtime._pending_terminals and len(requests) == 1
        finally:
            repository.retry_turn_terminal = retry
            repository.confirm_turn_terminal, repository.load_turn_status = confirm, status
            if stored == "conflict" and pending is not None:
                await save(pending)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
@pytest.mark.parametrize("error_kind", ["io", "busy", "corrupt", "sql", "cancel"])
def test_terminal_barrier_retries_only_storage_fault_once(tmp_path, after_commit, error_kind):
    from corki.storage.sqlite import StorageIntegrityError

    async def scenario():
        requests, attempts = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "bounded.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        repository = runtime._repository
        save, retry = repository.save_turn, repository.retry_turn_terminal
        if error_kind == "io":
            fault = OSError("storage unavailable")
        elif error_kind == "busy":
            fault = sqlite3.OperationalError("busy snapshot")
            fault.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
        elif error_kind == "corrupt":
            fault = StorageIntegrityError("conflicting terminal")
        elif error_kind == "sql":
            fault = sqlite3.OperationalError("invalid SQL")
            fault.sqlite_errorcode = sqlite3.SQLITE_ERROR
        else:
            fault = asyncio.CancelledError()

        async def failed_save(record):
            if record.status is not TurnStatus.RUNNING:
                raise OSError("initial failure")
            await save(record)

        async def fail_once(record):
            attempts.append(record)
            if len(attempts) == 1:
                if after_commit:
                    await retry(record)
                raise fault
            await retry(record)

        async def resume():
            return [event async for event in runtime.resume_pending()]

        try:
            repository.save_turn = failed_save
            repository.retry_turn_terminal = failed_save
            assert isinstance([event async for event in runtime.stream("input")][-1], TurnCompleted)
            pending = next(iter(runtime._pending_terminals.values()))
            repository.retry_turn_terminal = fail_once
            if error_kind in {"io", "busy"}:
                assert await resume() == []
                assert len(attempts) == 2
            else:
                with pytest.raises(type(fault)) as captured:
                    await resume()
                assert captured.value is fault
                assert len(attempts) == 1 and runtime._pending_terminals
                repository.retry_turn_terminal = retry
                assert await resume() == []
            assert runtime._pending_terminals == {}
            assert await repository.confirm_turn_terminal(pending)
            assert len(requests) == 1
        finally:
            repository.retry_turn_terminal = retry
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["resume", "new"])
def test_admission_drains_terminal_and_cancellation_joins_writer(tmp_path, action):
    async def scenario():
        requests = []
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "admission.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        repository = runtime._repository
        save, retry = repository.save_turn, repository.retry_turn_terminal
        sync_retry = repository._retry_turn_terminal

        async def failed_save(record):
            if record.status is not TurnStatus.RUNNING:
                raise OSError("initial terminal unavailable")
            await save(record)

        async def failed_retry(record):
            raise OSError("retry unavailable")

        async def admission_failure(record):
            if action == "new":
                # New work may proceed through transient old-terminal failures,
                # but malformed SQL must still block admission.
                raise sqlite3.OperationalError("retry unavailable")
            await failed_retry(record)

        def held_retry(record):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test did not release the terminal writer"
            sync_retry(record)

        async def proceed():
            stream = runtime.resume_pending() if action == "resume" else runtime.stream("next")
            return [event async for event in stream]

        work = None
        try:
            repository.save_turn = failed_save
            repository.retry_turn_terminal = failed_retry
            events = [event async for event in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted)
            pending = next(iter(runtime._pending_terminals.values()))
            prefix = await repository.load_items(runtime.thread_id)
            repository.save_turn = save
            repository.retry_turn_terminal = admission_failure
            with pytest.raises((OSError, sqlite3.OperationalError), match="retry unavailable"):
                await proceed()
            assert len(requests) == 1
            assert await repository.load_items(runtime.thread_id) == prefix
            repository.retry_turn_terminal = retry
            repository._retry_turn_terminal = held_retry
            work = asyncio.create_task(proceed())
            await asyncio.wait_for(entered.wait(), 3)
            work.cancel()
            await asyncio.sleep(0)
            assert not work.done(), "cancelled admission abandoned the storage writer"
            assert len(requests) == 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await work
            assert await repository.confirm_turn_terminal(pending)
            assert runtime._pending_terminals, "ambiguous cancelled acknowledgment was dropped"
            repository._retry_turn_terminal = sync_retry
            events = await proceed()
            assert runtime._pending_terminals == {}
            if action == "resume":
                assert events == [] and len(requests) == 1
            else:
                assert isinstance(events[-1], TurnCompleted) and len(requests) == 2
            assert (await repository.load_items(runtime.thread_id))[: len(prefix)] == prefix
        finally:
            release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            repository.retry_turn_terminal = retry
            repository._retry_turn_terminal = sync_retry
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("model_fails", [False, True])
def test_cold_resume_recovers_uncommitted_terminal_without_sampling(tmp_path, model_fails):
    async def scenario():
        requests = []

        class Model:
            def __init__(self, cold=False):
                self.cold = cold

            async def stream(self, request):
                requests.append(request)
                assert not self.cold, "recovery sampled an already finished task"
                if model_fails:
                    raise ModelError("original failure", kind=ModelErrorKind.AUTHENTICATION)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "original answer", request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

            async def aclose(self):
                pass

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=tmp_path / "terminal.db",
                home_path=tmp_path / "home",
                registry=ToolRegistry(),
                model=Model(cold=thread is not None),
                thread_id=thread,
            )

        source = create()
        save = source._repository.save_turn

        async def reject_terminal(record):
            if record.status is not TurnStatus.RUNNING:
                raise OSError("terminal storage unavailable")
            await save(record)

        source._repository.save_turn = reject_terminal
        source._repository.retry_turn_terminal = reject_terminal
        try:
            events = [event async for event in source.stream("input")]
            assert isinstance(events[-1], TurnFailed if model_fails else TurnCompleted)
            if model_fails:
                assert events[-1].error_kind == ModelErrorKind.AUTHENTICATION.value
            thread = source.thread_id
            pending = await source._repository.latest_running_turn(thread)
            assert pending is not None
            prefix = await source._repository.load_items(thread)
            assert len(requests) == 1
        finally:
            # Simulate loss of process-owned pending writes, then release fixture
            # resources. A graceful close now retries these records instead.
            source._pending_terminals.clear()
            await source.aclose()

        cold = create(thread)
        try:
            events = [event async for event in cold.resume_pending()]
            terminal = events[-1]
            if model_fails:
                assert isinstance(terminal, TurnFailed)
                assert terminal.error == "original failure"
                assert terminal.error_kind == ModelErrorKind.AUTHENTICATION.value
            else:
                assert isinstance(terminal, TurnCompleted)
                assert terminal.final_answer == "original answer"
            assert len(requests) == 1
            assert await cold._repository.latest_running_turn(thread) is None
            assert (await cold._repository.load_items(thread))[: len(prefix)] == prefix
            assert [event async for event in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("conflict", [False, True])
def test_shutdown_retries_pending_terminal_without_reclosing_execution(tmp_path, conflict):
    from dataclasses import replace

    from corki.storage.sqlite import StorageIntegrityError

    async def scenario():
        requests, model_closes, storage_closes, retry_attempts = [], [], [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                model_closes.append(True)

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "shutdown.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        repository = runtime._repository
        save, retry, close = repository.save_turn, repository.retry_turn_terminal, repository.close

        async def failed_save(record):
            if record.status is not TurnStatus.RUNNING:
                raise OSError("initial terminal write failed")
            await save(record)

        async def failed_retry(record):
            retry_attempts.append(record)
            raise OSError("pending writer unavailable")

        async def closing():
            storage_closes.append(True)
            await close()

        repository.save_turn = failed_save
        repository.retry_turn_terminal = failed_retry
        repository.close = closing
        try:
            events = [event async for event in runtime.stream("input")]
            assert isinstance(events[-1], TurnCompleted)
            pending = next(iter(runtime._pending_terminals.values()))
            assert len(retry_attempts) == 2
            retry_attempts.clear()
            with pytest.raises(OSError, match="pending writer unavailable"):
                await runtime.aclose()
            assert len(retry_attempts) == 2, "one barrier must not retry indefinitely"
            assert runtime._close_storage_pending
            assert model_closes == [True] and storage_closes == []
            assert (
                await repository.load_turn_status(runtime.thread_id, pending.id)
                is TurnStatus.RUNNING
            )
            repository.retry_turn_terminal = retry
            if conflict:
                other = replace(pending, final_answer="different owner result")
                await save(other)
                with pytest.raises(StorageIntegrityError, match="conflicts"):
                    await runtime.aclose()
                assert await repository.confirm_turn_terminal(other)
                assert runtime._pending_terminals and storage_closes == []
                # Repair only this fixture's injected conflict for owned teardown.
                await save(pending)
            await runtime.aclose()
            await runtime.aclose()
            assert runtime._pending_terminals == {}
            assert model_closes == [True] and storage_closes == [True]
            assert len(requests) == 1
            assert await repository.confirm_turn_terminal(pending)
        finally:
            repository.retry_turn_terminal = retry
            await runtime.aclose()

    asyncio.run(scenario())
