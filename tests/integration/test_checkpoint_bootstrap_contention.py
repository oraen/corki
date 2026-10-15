"""Busy checkpoint bootstrap cannot admit a model Turn or abandon its connection."""

import asyncio
import sqlite3
from contextlib import asynccontextmanager

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime, checkpoint_lifecycle
from corki.core import runtime as runtime_module
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


def test_actual_sqlite_busy_bootstrap_retries_without_replaying_turn(tmp_path, monkeypatch):
    async def scenario():
        busy = asyncio.Event()
        errors, savers, requests = [], [], []
        checkpoint_path = tmp_path / "checkpoints.db"
        holder = sqlite3.connect(checkpoint_path, timeout=0)
        holder.execute("CREATE TABLE sentinel (value TEXT)")
        holder.execute("INSERT INTO sentinel VALUES ('must remain')")
        holder.commit()
        holder.execute("BEGIN IMMEDIATE")
        factory = runtime_module.AsyncSqliteSaver.from_conn_string

        @asynccontextmanager
        async def context(path):
            async with factory(path) as saver:
                savers.append(saver)
                # Exercise SQLite's real fast-BUSY path deterministically, without
                # waiting for the driver's five-second handler in this fixture.
                async with saver.conn.execute("PRAGMA busy_timeout=0"):
                    pass
                execute = saver.conn.executescript

                @asynccontextmanager
                async def tracked(script):
                    try:
                        async with execute(script) as cursor:
                            async with saver.conn.execute("PRAGMA busy_timeout=5000"):
                                pass
                            yield cursor
                    except sqlite3.OperationalError as error:
                        errors.append(error)
                        busy.set()
                        raise

                saver.conn.executescript = tracked
                yield saver

        monkeypatch.setattr(runtime_module.AsyncSqliteSaver, "from_conn_string", context)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtimes = [
            await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False),
                database_path=tmp_path / f"{name}.db",
                registry=ToolRegistry(),
                model=Model(),
            )
            for name in ("A", "B")
        ]

        async def release():
            await asyncio.wait_for(busy.wait(), 3)
            assert not requests
            holder.rollback()

        async def consume(runtime):
            return [event async for event in runtime.stream("one input")]

        releasing = asyncio.create_task(release())
        consumers = [asyncio.create_task(consume(runtime)) for runtime in runtimes]
        try:
            results = await asyncio.wait_for(asyncio.gather(*consumers), 5)
            await releasing
            assert all(isinstance(result[-1], TurnCompleted) for result in results)
            assert errors and all(error.sqlite_errorcode == sqlite3.SQLITE_BUSY for error in errors)
            assert len(requests) == 2 and len(savers) == 2
            assert all(runtime._checkpoint_path == checkpoint_path for runtime in runtimes)
            assert holder.execute("SELECT value FROM sentinel").fetchall() == [("must remain",)]
            for runtime in runtimes:
                with sqlite3.connect(runtime._repository.path) as connection:
                    assert connection.execute("SELECT status FROM turns").fetchall() == [
                        ("completed",)
                    ]
        finally:
            holder.rollback()
            holder.close()
            releasing.cancel()
            for task in consumers:
                task.cancel()
            await asyncio.gather(releasing, *consumers, return_exceptions=True)
            await asyncio.gather(*(runtime.aclose() for runtime in runtimes))
            assert all(saver.conn._connection is None for saver in savers)

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["deadline", "cancel", "cancel_active", "close"])
def test_busy_bootstrap_failure_or_cancel_joins_cleanup_before_reuse(tmp_path, monkeypatch, action):
    async def scenario():
        busy, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        savers, requests, attempts = [], [], []
        factory = runtime_module.AsyncSqliteSaver.from_conn_string
        error = sqlite3.OperationalError("fixture busy")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        if action == "deadline":
            monkeypatch.setattr(checkpoint_lifecycle, "_SETUP_BUSY_RETRY_SECONDS", 0.025)

        @asynccontextmanager
        async def context(path):
            async with factory(path) as saver:
                savers.append(saver)
                first = len(savers) == 1
                if first:

                    async def setup():
                        attempts.append(True)
                        busy.set()
                        raise error

                    saver.setup = setup
                try:
                    yield saver
                finally:
                    if first:
                        closing.set()
                        await release.wait()

        monkeypatch.setattr(runtime_module.AsyncSqliteSaver, "from_conn_string", context)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False),
                database_path=tmp_path / "sessions.db",
                registry=ToolRegistry(),
                model=Model(),
                thread_id=thread,
            )

        runtime = await create()

        async def consume(owner):
            return [event async for event in owner.stream("one input")]

        consumer = asyncio.create_task(consume(runtime))
        action_task = None
        try:
            await asyncio.wait_for(busy.wait(), 3)
            if action == "cancel":
                consumer.cancel()
            elif action in ("cancel_active", "close"):
                action_task = asyncio.create_task(
                    runtime.aclose() if action == "close" else runtime.cancel_active()
                )
            await asyncio.wait_for(closing.wait(), 3)
            if action == "cancel":
                consumer.cancel()  # A second cancellation cannot abandon cleanup.
                await asyncio.sleep(0)
            assert not consumer.done() and not requests
            assert runtime._compiled is None and runtime._checkpointer is None
            with sqlite3.connect(runtime._repository.path) as connection:
                assert connection.execute("SELECT COUNT(*) FROM turns").fetchone() == (0,)
            release.set()
            with pytest.raises(
                sqlite3.OperationalError if action == "deadline" else asyncio.CancelledError
            ) as caught:
                await consumer
            if action == "deadline":
                assert caught.value is error
            if action_task is not None:
                await action_task
            assert savers[0].conn._connection is None and len(savers) == 1
            if action == "close":
                runtime = await create(runtime.thread_id)
            assert isinstance((await consume(runtime))[-1], TurnCompleted)
            assert len(savers) == 2 and len(requests) == 1
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            if action_task is not None:
                await asyncio.gather(action_task, return_exceptions=True)
            await runtime.aclose()
            assert all(saver.conn._connection is None for saver in savers)

    asyncio.run(scenario())
