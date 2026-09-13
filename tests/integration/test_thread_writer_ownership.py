"""Live Runtime ownership must precede recovery writes to shared durable history."""

import asyncio
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

import corki
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


class AnswerModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


def test_second_runtime_cannot_write_while_first_thread_owner_remains_open(tmp_path):
    async def scenario():
        database, thread = tmp_path / "history.db", new_thread_id()
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        first = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            thread_id=thread,
            home_path=tmp_path / "home",
            model=AnswerModel(),
        )
        second = None
        try:
            assert isinstance([event async for event in first.stream("first")][-1], TurnCompleted)
            original = await first._repository.load_items(thread)
            model = AnswerModel()
            second = LangGraphRuntime.create(
                settings=settings,
                database_path=database,
                thread_id=thread,
                home_path=tmp_path / "home",
                model=model,
            )
            with pytest.raises(RuntimeError, match="active writer"):
                _ = [event async for event in second.stream("competing input")]
            assert not model.requests
            assert await first._repository.load_items(thread) == original
        finally:
            if second is not None:
                await second.aclose()
            await first.aclose()

    asyncio.run(scenario())


def test_failed_initialization_releases_writer_and_retry_reacquires_it(tmp_path, monkeypatch):
    from corki.core import runtime as runtime_module

    async def scenario():
        database, thread = tmp_path / "history.db", new_thread_id()
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        setup = runtime_module.setup_checkpoint
        failed = False

        async def fail_once(saver):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("fixture initialization failed")
            await setup(saver)

        monkeypatch.setattr(runtime_module, "setup_checkpoint", fail_once)
        first, second = [
            LangGraphRuntime.create(
                settings=settings,
                database_path=database,
                thread_id=thread,
                home_path=tmp_path / "home",
                model=AnswerModel(),
            )
            for _ in range(2)
        ]
        try:
            with pytest.raises(OSError, match="fixture initialization"):
                _ = [event async for event in first.stream("not admitted")]
            assert not first._writer.held
            assert isinstance(
                [event async for event in second.stream("other owner")][-1], TurnCompleted
            )
            with pytest.raises(RuntimeError, match="active writer"):
                _ = [event async for event in first.stream("not admitted either")]
            await second.aclose()
            assert isinstance([event async for event in first.stream("retry")][-1], TurnCompleted)
        finally:
            await second.aclose()
            await first.aclose()

    asyncio.run(scenario())


def test_cancellation_joins_acquisition_then_releases_returned_guard(tmp_path, monkeypatch):
    async def scenario():
        database, thread = tmp_path / "history.db", new_thread_id()
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        first, second = [
            LangGraphRuntime.create(
                settings=settings,
                database_path=database,
                thread_id=thread,
                home_path=tmp_path / "home",
                model=AnswerModel(),
            )
            for _ in range(2)
        ]
        acquire = first._writer._coordinator.acquire

        def delayed_acquire(thread_id):
            guard = acquire(thread_id)
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                guard.close()
                raise TimeoutError("fixture did not release writer acquisition")
            return guard

        monkeypatch.setattr(first._writer._coordinator, "acquire", delayed_acquire)

        async def consume():
            return [event async for event in first.stream("cancel acquisition")]

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            consumer.cancel()
            with pytest.raises(RuntimeError, match="active writer"):
                _ = [event async for event in second.stream("too early")]
            assert not consumer.done()
            consumer.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 3)
            assert not first._writer.held
            assert isinstance(
                [event async for event in second.stream("after cancellation")][-1], TurnCompleted
            )
        finally:
            release.set()
            await asyncio.gather(consumer, return_exceptions=True)
            await second.aclose()
            await first.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_close_holds_writer_until_model_cleanup_finishes(tmp_path, cancel_waiter):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Model(AnswerModel):
            async def aclose(self):
                entered.set()
                await release.wait()

        database, thread = tmp_path / "history.db", new_thread_id()
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        first = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            thread_id=thread,
            home_path=tmp_path / "home",
            model=Model(),
        )
        second = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            thread_id=thread,
            home_path=tmp_path / "home",
            model=AnswerModel(),
        )
        closing = None
        try:
            assert isinstance([event async for event in first.stream("first")][-1], TurnCompleted)
            closing = asyncio.create_task(first.aclose())
            await asyncio.wait_for(entered.wait(), 3)
            if cancel_waiter:
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing
                closing = asyncio.create_task(first.aclose())
            with pytest.raises(RuntimeError, match="active writer"):
                _ = [event async for event in second.stream("before cleanup")]
            release.set()
            await asyncio.wait_for(closing, 3)
            assert isinstance(
                [event async for event in second.stream("after cleanup")][-1], TurnCompleted
            )
        finally:
            release.set()
            await first.aclose()
            await second.aclose()
            if closing is not None:
                await closing

    asyncio.run(scenario())


@pytest.mark.parametrize("site", ["create_thread", "load_thread_session_id"])
def test_thread_creation_failure_releases_writer_before_retry(tmp_path, monkeypatch, site):
    async def scenario():
        database, thread = tmp_path / "history.db", new_thread_id()
        first, second = [
            LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=database,
                thread_id=thread,
                home_path=tmp_path / "home",
                model=AnswerModel(),
            )
            for _ in range(2)
        ]
        original = getattr(first._repository, site)

        async def fail(*args, **kwargs):
            raise OSError("fixture thread creation failed")

        try:
            monkeypatch.setattr(first._repository, site, fail)
            with pytest.raises(OSError, match="fixture thread creation"):
                await first._ensure_thread()
            assert not first._writer.held and not first._thread_created
            assert isinstance([event async for event in second.stream("owner")][-1], TurnCompleted)
            await second.aclose()
            monkeypatch.setattr(first._repository, site, original)
            assert isinstance([event async for event in first.stream("retry")][-1], TurnCompleted)
        finally:
            await first.aclose()
            await second.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("site", ["control", "model", "checkpoint"])
def test_cleanup_error_is_reported_after_writer_release(tmp_path, monkeypatch, site):
    from corki.core import runtime as runtime_module

    async def scenario():
        database, thread = tmp_path / "history.db", new_thread_id()
        first, second = [
            LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=database,
                thread_id=thread,
                home_path=tmp_path / "home",
                model=AnswerModel(),
            )
            for _ in range(2)
        ]
        target = {
            "control": (first._turn_updates, "aclose"),
            "model": (first._model, "aclose"),
            "checkpoint": (runtime_module, "close_checkpoint"),
        }[site]
        original = getattr(*target)
        held_during_cleanup = []

        async def fail_after_cleanup(*args, **kwargs):
            held_during_cleanup.append(first._writer.held)
            await original(*args, **kwargs)
            raise OSError("fixture cleanup failed")

        try:
            assert isinstance([event async for event in first.stream("owner")][-1], TurnCompleted)
            monkeypatch.setattr(*target, fail_after_cleanup)
            with pytest.raises(OSError, match="fixture cleanup failed"):
                await first.aclose()
            monkeypatch.setattr(*target, original)
            assert held_during_cleanup == [True]
            assert not first._writer.held and first._checkpointer is None
            assert isinstance(
                [event async for event in second.stream("after failed close")][-1], TurnCompleted
            )
        finally:
            monkeypatch.setattr(*target, original)
            await asyncio.gather(first.aclose(), second.aclose(), return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["idle", "tool"])
def test_process_death_releases_writer_and_does_not_replay_unknown_tool(tmp_path, mode):
    async def scenario():
        database, thread = tmp_path / "history.db", new_thread_id()
        fixture = Path(__file__).parents[1] / "fixtures" / "thread_writer_runtime.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(database),
            thread,
            mode,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(Path(corki.__file__).resolve().parent.parent)},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        runtime = None
        calls = []

        class Tool:
            spec = ToolSpec("hold", "Must not repeat", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "incorrect replay")

        try:
            assert await asyncio.wait_for(process.stdout.readline(), 10) == b"READY\n"
            registry = ToolRegistry()
            registry.register(Tool())
            model = AnswerModel()
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=database,
                thread_id=thread,
                home_path=tmp_path / "home",
                registry=registry,
                model=model,
            )
            with pytest.raises(RuntimeError, match="active writer"):
                _ = [event async for event in runtime.stream("must not be admitted")]
            assert not model.requests
            independent = LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=database,
                home_path=tmp_path / "home",
                model=AnswerModel(),
            )
            try:
                assert isinstance(
                    [event async for event in independent.stream("independent thread")][-1],
                    TurnCompleted,
                )
                assert process.returncode is None
            finally:
                await independent.aclose()
            process.kill()
            await asyncio.wait_for(process.communicate(), 5)
            events = [
                event
                async for event in (
                    runtime.resume_pending() if mode == "tool" else runtime.stream("after exit")
                )
            ]
            assert isinstance(events[-1], TurnCompleted)
            assert len(model.requests) == 1 and not calls
            if mode == "tool":
                assert (tmp_path / "effect.txt").read_text() == "performed once"
                results = [
                    item
                    for item in await runtime._repository.load_items(thread)
                    if isinstance(item, ToolResultItem)
                ]
                assert len(results) == 1 and results[0].is_error
                assert "outcome is unknown" in results[0].content
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            if runtime is not None:
                await runtime.aclose()

    asyncio.run(scenario())


def test_opening_unrelated_runtime_does_not_interrupt_an_active_tool_ledger(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        entered, release = asyncio.Event(), asyncio.Event()
        call = ToolCall(new_tool_call_id(), "hold", {})

        class Tool:
            spec = ToolSpec("hold", "Fixture blocking tool", {"type": "object"})

            async def execute(self, invocation, context):
                entered.set()
                await release.wait()
                return ToolResult(invocation.id, invocation.name, "finished")

        class Model(AnswerModel):
            async def stream(self, request):
                if not self.requests:
                    self.requests.append(request)
                    yield ModelCompleted(
                        (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                    )
                else:
                    async for event in super().stream(request):
                        yield event

        registry = ToolRegistry()
        registry.register(Tool())
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        first = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            home_path=tmp_path / "home",
            registry=registry,
            model=Model(),
        )
        second = None

        async def consume():
            return [event async for event in first.stream("run tool")]

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            second = LangGraphRuntime.create(
                settings=settings,
                database_path=database,
                home_path=tmp_path / "home",
                model=AnswerModel(),
            )
            with sqlite3.connect(database) as db:
                assert db.execute(
                    "SELECT status FROM tool_executions WHERE call_id=?", (call.id,)
                ).fetchone() == ("running",)
        finally:
            release.set()
            events = await asyncio.wait_for(consumer, 3)
            assert isinstance(events[-1], TurnCompleted)
            if second is not None:
                await second.aclose()
            await first.aclose()

    asyncio.run(scenario())
