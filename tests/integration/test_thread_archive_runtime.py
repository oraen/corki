"""Archive is a real Runtime lifecycle, not a memory-mode alias or history deletion."""

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
from corki.protocol.ids import new_thread_id
from corki.protocol.items import AssistantMessageItem, ContextItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


class AnswerModel:
    def __init__(self):
        self.requests = []
        self.closed = False

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        self.closed = True


async def runtime_for(tmp_path, **kwargs):
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        model=kwargs.pop("model", AnswerModel()),
        **kwargs,
    )


def test_archive_closes_runtime_preserves_history_and_requires_explicit_unarchive(tmp_path):
    async def scenario():
        model = AnswerModel()
        runtime = await runtime_for(tmp_path, model=model)
        successor = None
        try:
            assert isinstance(
                [event async for event in runtime.stream("remember this")][-1], TurnCompleted
            )
            history = await runtime._repository.load_items(runtime.thread_id)
            archived = await runtime.archive()
            assert model.closed and not runtime._writer.held
            assert archived.id == runtime.thread_id and archived.archived_at is not None
            assert await runtime._repository.load_items(runtime.thread_id) == history
            assert await runtime._repository.latest_thread() is None
            with pytest.raises(ValueError, match="archived"):
                runtime._repository.resolve_thread(str(runtime.thread_id), tmp_path)
            successor_model = AnswerModel()
            successor = await runtime_for(
                tmp_path, thread_id=runtime.thread_id, model=successor_model
            )
            with pytest.raises(ValueError, match="archived"):
                _ = [event async for event in successor.stream("not admitted")]
            assert not successor_model.requests
            assert not successor._writer.held
            restored = await successor.unarchive()
            assert restored.archived_at is None
            assert isinstance(
                [event async for event in successor.stream("continue")][-1], TurnCompleted
            )
            conversation = tuple(item for item in history if not isinstance(item, ContextItem))
            previous_ids = {item.id for item in conversation}
            replayed = tuple(
                item for item in successor_model.requests[0].items if item.id in previous_ids
            )
            # Request projection enriches provider content-kind metadata; the
            # canonical archive must still remain byte-for-byte semantically equal.
            assert tuple((type(item), item.id, item.content) for item in replayed) == tuple(
                (type(item), item.id, item.content) for item in conversation
            )
            history_ids = {item.id for item in history}
            assert (
                tuple(
                    item
                    for item in await successor._repository.load_items(successor.thread_id)
                    if item.id in history_ids
                )
                == history
            )
        finally:
            if successor is not None:
                await successor.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


def test_empty_runtime_cannot_be_archived_or_closed_by_failed_preflight(tmp_path):
    async def scenario():
        runtime = await runtime_for(tmp_path)
        try:
            await runtime._ensure_ready()
            with pytest.raises(ValueError, match="materialized history"):
                await runtime.archive()
            assert not runtime._closed and runtime._writer.held
            assert isinstance(
                [event async for event in runtime.stream("materialize")][-1], TurnCompleted
            )
            await runtime.archive()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_explicit_core_resume_keeps_archive_state_and_blocks_unarchive_until_closed(tmp_path):
    async def scenario():
        original = await runtime_for(tmp_path)
        resumed = None
        try:
            _ = [event async for event in original.stream("original")]
            await original.archive()
            resumed = await runtime_for(
                tmp_path, thread_id=original.thread_id, include_archived=True
            )
            assert isinstance(
                [event async for event in resumed.stream("internal continuation")][-1],
                TurnCompleted,
            )
            record = await resumed._archive_store.read(resumed.thread_id)
            assert record.archived_at is not None
            assert await resumed._repository.latest_thread() is None
            with pytest.raises(RuntimeError, match="active writer"):
                await resumed.unarchive()
            await resumed.aclose()
            restored = await resumed.unarchive()
            assert restored.archived_at is None
        finally:
            if resumed is not None:
                await resumed.aclose()
            await original.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_archive_joins_active_turn_and_model_cleanup_before_mutating(tmp_path, cancel_waiter):
    async def scenario():
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class BlockingModel(AnswerModel):
            async def stream(self, request):
                entered.set()
                await asyncio.Event().wait()
                yield  # pragma: no cover

            async def aclose(self):
                cleaning.set()
                await release.wait()
                await super().aclose()

        runtime = await runtime_for(tmp_path, model=BlockingModel())

        async def consume():
            return [event async for event in runtime.stream("active request")]

        consumer = asyncio.create_task(consume())
        archiving = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            archiving = asyncio.create_task(runtime.archive())
            await asyncio.wait_for(cleaning.wait(), 3)
            if cancel_waiter:
                archiving.cancel()
            record = await runtime._archive_store.read(runtime.thread_id)
            assert record.archived_at is None
            assert runtime._writer.held and not archiving.done()
            with pytest.raises(RuntimeError, match="active writer"):
                await runtime._archive_store.archive(runtime.thread_id)
            release.set()
            if cancel_waiter:
                with pytest.raises(asyncio.CancelledError):
                    await archiving
            else:
                await archiving
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert db.execute("SELECT status FROM turns").fetchall() == [("cancelled",)]
            assert (await runtime._archive_store.read(runtime.thread_id)).archived_at is not None
            assert not runtime._writer.held
        finally:
            release.set()
            await runtime.aclose()
            await asyncio.gather(
                consumer, *([archiving] if archiving is not None else []), return_exceptions=True
            )

    asyncio.run(scenario())


def test_archive_shutdown_timeout_does_not_grant_write_ownership(tmp_path, monkeypatch):
    from corki.sessions import archive as archive_module

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Model(AnswerModel):
            async def aclose(self):
                entered.set()
                await release.wait()

        runtime = await runtime_for(tmp_path, model=Model())
        try:
            _ = [event async for event in runtime.stream("history")]
            monkeypatch.setattr(archive_module, "SHUTDOWN_TIMEOUT_SECONDS", 0.01)
            with pytest.raises(RuntimeError, match="active writer"):
                await runtime.archive()
            assert entered.is_set()
            assert (await runtime._archive_store.read(runtime.thread_id)).archived_at is None
            release.set()
            await runtime.aclose()
            assert (await runtime.archive()).archived_at is not None
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


def test_archive_is_independent_of_source_memory_mode_and_source_recency(tmp_path):
    async def scenario():
        runtime = await runtime_for(tmp_path)
        try:
            _ = [event async for event in runtime.stream("source")]
            with sqlite3.connect(tmp_path / "history.db") as db:
                db.execute("UPDATE threads SET updated_at='2026-01-01T00:00:00Z'")
                before = db.execute(
                    "SELECT memory_mode, source, updated_at FROM threads"
                ).fetchone()
            archived = await runtime.archive()
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert (
                    db.execute("SELECT memory_mode, source, updated_at FROM threads").fetchone()
                    == before
                )
            assert archived.updated_at == before[2]
            restored = await runtime.unarchive()
            assert restored.archived_at is None and restored.updated_at > before[2]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_archive_metadata_fault_rolls_back_before_successor_can_resume(tmp_path, monkeypatch):
    async def scenario():
        runtime = await runtime_for(tmp_path)
        successor = None
        try:
            _ = [event async for event in runtime.stream("history")]
            store = runtime._archive_store
            before = await store.read(runtime.thread_id)
            original = store._record
            calls = 0

            def fail_after_update(connection, thread_id):
                nonlocal calls
                calls += 1
                result = original(connection, thread_id)
                if calls == 3:  # host preflight, transaction read, then post-update read
                    assert result.archived_at is not None
                    raise OSError("fixture archive transaction failed")
                return result

            monkeypatch.setattr(store, "_record", fail_after_update)
            with pytest.raises(OSError, match="fixture archive transaction"):
                await runtime.archive()
            assert calls == 3
            assert await store.read(runtime.thread_id) == before
            successor = await runtime_for(tmp_path, thread_id=runtime.thread_id)
            assert isinstance(
                [event async for event in successor.stream("after rollback")][-1], TurnCompleted
            )
        finally:
            if successor is not None:
                await successor.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


def test_cancelled_unarchive_joins_database_worker_before_releasing_writer(tmp_path, monkeypatch):
    async def scenario():
        runtime = await runtime_for(tmp_path)
        successor = None
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        restoring = None
        try:
            _ = [event async for event in runtime.stream("history")]
            await runtime.archive()
            store = runtime._archive_store
            original = store._write

            def delayed_write(*args):
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(5):
                    raise TimeoutError("fixture did not release archive worker")
                return original(*args)

            monkeypatch.setattr(store, "_write", delayed_write)
            restoring = asyncio.create_task(runtime.unarchive())
            await asyncio.wait_for(entered.wait(), 3)
            restoring.cancel()
            successor = await runtime_for(tmp_path, thread_id=runtime.thread_id)
            with pytest.raises(RuntimeError, match="active writer"):
                _ = [event async for event in successor.stream("too early")]
            assert not restoring.done()
            restoring.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await restoring
            assert (await store.read(runtime.thread_id)).archived_at is None
            assert isinstance(
                [event async for event in successor.stream("after restore")][-1], TurnCompleted
            )
        finally:
            release.set()
            if restoring is not None:
                await asyncio.gather(restoring, return_exceptions=True)
            if successor is not None:
                await successor.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


def test_external_writer_blocks_archive_then_crash_archive_restore_does_not_replay_tool(tmp_path):
    async def scenario():
        thread = new_thread_id()
        fixture = Path(__file__).parents[1] / "fixtures" / "thread_writer_runtime.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(tmp_path / "history.db"),
            thread,
            "tool",
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(Path(corki.__file__).resolve().parent.parent)},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        host = resumed = None
        calls = []

        class Tool:
            spec = ToolSpec("hold", "Must not repeat", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "incorrect replay")

        try:
            assert await asyncio.wait_for(process.stdout.readline(), 10) == b"READY\n"
            host = await runtime_for(tmp_path, thread_id=thread)
            with pytest.raises(RuntimeError, match="active writer"):
                await host.archive()
            assert (await host._archive_store.read(thread)).archived_at is None
            assert process.returncode is None
            process.kill()
            await asyncio.wait_for(process.communicate(), 5)
            await host.archive()
            await host.unarchive()
            registry = ToolRegistry()
            registry.register(Tool())
            resumed = await runtime_for(tmp_path, thread_id=thread, registry=registry)
            assert isinstance(
                [event async for event in resumed.resume_pending()][-1], TurnCompleted
            )
            assert not calls and (tmp_path / "effect.txt").read_text() == "performed once"
            results = tuple(
                item
                for item in await resumed._repository.load_items(thread)
                if isinstance(item, ToolResultItem)
            )
            assert len(results) == 1 and results[0].is_error
            assert "outcome is unknown" in results[0].content
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            if resumed is not None:
                await resumed.aclose()
            if host is not None:
                await host.aclose()

    asyncio.run(scenario())
