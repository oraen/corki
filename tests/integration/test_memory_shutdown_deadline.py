import asyncio
import json
import shlex
import shutil
import sqlite3
import sys
import traceback
from contextlib import suppress

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import agent
from corki.memory.agent_shutdown import _RETAINED_OWNERS
from corki.memory.workspace_lease import MemoryWorkspaceBusy, WorkspaceLeases
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("stop", ["completed", "cancelled", "heartbeat"])
@pytest.mark.parametrize("late_failure", ["none", "close", "copy"])
def test_timeout_retains_worker_and_terminal_without_late_publication(
    tmp_path, monkeypatch, stop, late_failure, memory_worker_state_dirs
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        children = []
        sql_failures = []
        original_connect = sqlite3.connect

        class DiagnosticConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                try:
                    return super().execute(sql, parameters)
                except sqlite3.OperationalError as exc:
                    sql_failures.append(
                        (sql, getattr(exc, "sqlite_errorname", None), traceback.format_exc())
                    )
                    raise

        def diagnostic_connect(*args, **kwargs):
            kwargs.setdefault("factory", DiagnosticConnection)
            return original_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", diagnostic_connect)
        root, database = tmp_path / "memories", tmp_path / "sessions.db"
        monkeypatch.setattr("corki.memory.agent._SHUTDOWN_TIMEOUT_SECONDS", 0.03, raising=False)
        original_blocking = agent._blocking

        async def blocking(function, *args):
            if function is shutil.rmtree and late_failure == "copy":
                raise OSError("injected copy cleanup failure")
            return await original_blocking(function, *args)

        monkeypatch.setattr(agent, "_blocking", blocking)

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("main ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.calls == 1:
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec_command",
                        {
                            "cmd": shlex.join(
                                [sys.executable, "-c", "import time; time.sleep(60)"]
                            ),
                            "login": False,
                            "yield_time_ms": 250,
                        },
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    text = json.dumps({"memory": "late", "memory_summary": "late", "skills": []})
                    yield ModelCompleted((AssistantMessageItem(text, turn, step),))

            async def aclose(self):
                raise AssertionError("borrowed transport must not be closed")

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_consolidation_model="fixture",
            ),
            database_path=database,
            memory_root=root,
            model=Main(),
            memory_model=Memory(),
        )
        original_close = LangGraphRuntime.aclose

        async def held_close(child):
            if child is runtime:
                return await original_close(child)
            children.append(child)
            entered.set()
            await release.wait()
            await original_close(child)
            if late_failure == "close":
                raise OSError("late shutdown acknowledgement failed")

        monkeypatch.setattr(LangGraphRuntime, "aclose", held_close)
        if stop == "heartbeat":

            async def heartbeat(claim):
                await entered.wait()
                raise OSError("heartbeat failed while closing")

            monkeypatch.setattr(runtime._memory_service, "_heartbeat", heartbeat)
        try:
            terminal = [e async for e in runtime.stream("main")][-1]
            assert isinstance(terminal, TurnCompleted), (terminal, sql_failures)
            await asyncio.wait_for(entered.wait(), 3)
            assert children[0]._process_manager.list_background_terminals()
            task = runtime._memory_service._task
            if stop == "cancelled":
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
            done, _ = await asyncio.wait((task,), timeout=0.8)
            assert task in done, "background pass ignored its worker shutdown deadline"
            if stop == "cancelled":
                with pytest.raises(asyncio.CancelledError):
                    task.result()
            else:
                assert task.result().failed == 1
            retained = runtime._memory_service.retained_workers
            assert len(retained) == 1 and retained[0].status == "closing"
            copy = retained[0].working_copy
            assert copy.exists() and children[0]._process_manager.list_background_terminals()
            with sqlite3.connect(database) as db:
                before = db.execute(
                    "SELECT status,ownership_token FROM memory_jobs WHERE job_key='global'"
                ).fetchone()
            assert before[0] == "running" and before[1]
            with pytest.raises(MemoryWorkspaceBusy):
                WorkspaceLeases((root,))
            release.set()
            remaining = await runtime._memory_service._agent_shutdowns.settle(timeout=2)
            assert not children[0]._process_manager.list_background_terminals()
            if late_failure == "close":
                with pytest.raises(MemoryWorkspaceBusy):
                    WorkspaceLeases((root,))
            else:
                WorkspaceLeases((root,)).close()
            if late_failure != "none":
                assert len(remaining) == 1 and remaining[0].status == "failed"
                expected = "late shutdown" if late_failure == "close" else "copy cleanup"
                assert expected in remaining[0].error and copy.exists()
            else:
                assert not remaining and not copy.exists()
            assert not (root / "MEMORY.md").exists()
            from corki.memory import git_baseline

            assert "MEMORY.md" not in git_baseline.read(root)
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT status,ownership_token FROM memory_jobs WHERE job_key='global'"
                    ).fetchone()
                    == before
                )
        finally:
            release.set()
            if runtime._memory_service._task is not None:
                await asyncio.gather(runtime._memory_service._task, return_exceptions=True)
            for child in children:
                await original_close(child)
            await original_close(runtime)
            for copy in memory_worker_state_dirs:
                if copy.exists():
                    shutil.rmtree(copy)  # Fixture-confirmed close; never an unverified live worker.
            # The injected wrapper/copy failures are fixture-owned; actual Runtime
            # close was explicitly confirmed above before dropping diagnostic records.
            owner = runtime._memory_service._agent_shutdowns
            for worker in tuple(owner._workers.values()):
                # Injected cleanup failure; lease release precedes file removal.
                with suppress(OSError):
                    await worker.cleanup()  # Actual close was fixture-confirmed above.
            owner._workers.clear()
            _RETAINED_OWNERS.discard(owner)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_close_waiter", [False, True])
def test_parent_shutdown_is_bounded_and_late_close_does_not_reclose_owned_model(
    tmp_path, monkeypatch, cancel_close_waiter
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        host_wait, host_release = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(agent, "_SHUTDOWN_TIMEOUT_SECONDS", 0.03)

        class Model:
            def __init__(self):
                self.closed = 0

            async def stream(self, request):
                text = json.dumps({"memory": "late", "memory_summary": "late", "skills": []})
                yield ModelCompleted(
                    (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                self.closed += 1

        main, memory = Model(), Model()
        created = iter((main, memory))
        monkeypatch.setattr("corki.core.runtime._create_model", lambda *args: next(created))
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=tmp_path / "memories",
        )
        assert runtime._memory_service._close_model
        original_close = LangGraphRuntime.aclose
        owner = runtime._memory_service._agent_shutdowns
        original_settle = owner.settle
        children = []

        async def held_close(child):
            if child is runtime:
                return await original_close(child)
            children.append(child)
            entered.set()
            await release.wait()
            await original_close(child)

        async def bounded_host_wait():
            host_wait.set()
            if cancel_close_waiter:
                await host_release.wait()
            return await original_settle(timeout=0.03)

        monkeypatch.setattr(LangGraphRuntime, "aclose", held_close)
        monkeypatch.setattr(owner, "settle", bounded_host_wait)
        try:
            assert isinstance([e async for e in runtime.stream("main")][-1], TurnCompleted)
            await asyncio.wait_for(entered.wait(), 1)
            assert (await runtime._memory_service.wait()).failed == 1
            if cancel_close_waiter:
                closer = asyncio.create_task(runtime.aclose())
                try:
                    await asyncio.wait_for(host_wait.wait(), 1)
                    closer.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await closer
                    assert main.closed == memory.closed == 0
                    assert owner in _RETAINED_OWNERS
                    assert owner.retained[0].working_copy.exists()
                finally:
                    host_release.set()
                    if not closer.done():
                        closer.cancel()
                    await asyncio.gather(closer, return_exceptions=True)
            await asyncio.wait_for(runtime.aclose(), 1)
            assert main.closed == memory.closed == 1
            assert owner in _RETAINED_OWNERS
            assert owner.retained[0].status == "closing"
            assert owner.retained[0].working_copy.exists()
            release.set()
            assert not await original_settle(timeout=1)
            assert owner not in _RETAINED_OWNERS
            assert main.closed == memory.closed == 1
            assert not (tmp_path / "memories/MEMORY.md").exists()
        finally:
            host_release.set()
            release.set()
            for child in children:
                await original_close(child)
            await original_close(runtime)
            await original_settle(timeout=1)

    asyncio.run(scenario())
