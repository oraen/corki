"""Ephemeral execution must never create canonical disk history, even before close."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

import corki
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.agent import run_agent
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
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


@pytest.mark.parametrize("ephemeral", [False, True])
def test_ephemeral_runtime_keeps_cross_turn_context_without_disk_state(tmp_path, ephemeral):
    async def scenario():
        database = tmp_path / "state" / "history.db"
        model = AnswerModel()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=database,
            home_path=tmp_path / "home",
            model=model,
            ephemeral=ephemeral,
        )
        try:
            assert database.parent.exists() is (not ephemeral)
            for prompt in ("remember this during this session", "continue"):
                assert isinstance(
                    [event async for event in runtime.stream(prompt)][-1], TurnCompleted
                )
                assert database.parent.exists() is (not ephemeral)
            assert tuple(
                item.content
                for item in model.requests[-1].items
                if isinstance(item, UserMessageItem)
            ) == ("remember this during this session", "continue")
        finally:
            await runtime.aclose()
        assert database.parent.exists() is (not ephemeral)

    asyncio.run(scenario())


def test_ephemeral_close_joins_tool_cleanup_after_waiter_cancellation(tmp_path):
    async def scenario():
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Tool:
            spec = ToolSpec("hold", "Wait until cancelled", {"type": "object"})

            async def execute(self, call, context):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()

        class Model(AnswerModel):
            async def stream(self, request):
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), "hold", {}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "state" / "history.db",
            home_path=tmp_path / "home",
            model=Model(),
            registry=registry,
            ephemeral=True,
        )

        async def consume():
            return [event async for event in runtime.stream("hold tool")]

        consumer = asyncio.create_task(consume())
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.wait_for(cleaning.wait(), 3)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert not runtime._repository._closed
            assert not (tmp_path / "state").exists()
            release.set()
            await runtime.aclose()
            assert runtime._repository._closed and runtime._checkpointer is None
            with pytest.raises(asyncio.CancelledError):
                await consumer
        finally:
            release.set()
            await runtime.aclose()
            await asyncio.gather(
                consumer, *([closing] if closing is not None else []), return_exceptions=True
            )

    asyncio.run(scenario())


def test_failed_runtime_composition_closes_new_volatile_connection(tmp_path, monkeypatch):
    from corki.core import runtime as runtime_module
    from corki.storage.volatile import VolatileSessionRepository

    created = []
    original = VolatileSessionRepository.__init__

    def capture(self):
        original(self)
        created.append(self)

    def fail(**kwargs):
        raise ValueError("fixture graph composition failed")

    monkeypatch.setattr(VolatileSessionRepository, "__init__", capture)
    monkeypatch.setattr(runtime_module, "ContextWindowManager", fail)
    try:
        with pytest.raises(ValueError, match="fixture graph composition"):
            LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=tmp_path / "state" / "history.db",
                home_path=tmp_path / "home",
                model=AnswerModel(),
                ephemeral=True,
            )
        assert len(created) == 1 and created[0]._closed
    finally:
        for repository in created:
            repository._close()


@pytest.mark.parametrize("phase", ["model", "tool"])
def test_ephemeral_process_crash_leaves_no_canonical_history(tmp_path, phase):
    async def scenario():
        fixture = Path(__file__).parents[1] / "fixtures" / "ephemeral_runtime.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(tmp_path),
            phase,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(Path(corki.__file__).resolve().parent.parent)},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert await asyncio.wait_for(process.stdout.readline(), 10) == b"READY\n"
            assert not (tmp_path / "state").exists()
            process.kill()
            await asyncio.wait_for(process.communicate(), 5)
            assert not (tmp_path / "state").exists()
            if phase == "tool":
                assert (tmp_path / "effect.txt").read_text() == "explicit tool output"
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_ephemeral_initialization_cleanup_and_retry(tmp_path, monkeypatch, cancel):
    from corki.core import runtime as runtime_module

    async def scenario():
        entered = asyncio.Event()
        savers = []
        setup = runtime_module.setup_checkpoint

        async def fail_once(saver):
            savers.append(saver)
            await setup(saver)
            if len(savers) == 1:
                entered.set()
                if cancel:
                    await asyncio.Event().wait()
                raise OSError("fixture initialization failed")

        monkeypatch.setattr(runtime_module, "setup_checkpoint", fail_once)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "state" / "history.db",
            home_path=tmp_path / "home",
            model=AnswerModel(),
            ephemeral=True,
        )

        async def consume():
            return [event async for event in runtime.stream("request")]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else OSError):
                await task
            assert not runtime._repository._closed and runtime._checkpointer is None
            with pytest.raises(ValueError):
                await savers[0].conn.execute("SELECT 1")
            assert isinstance([event async for event in runtime.stream("retry")][-1], TurnCompleted)
            assert len(savers) == 2 and not (tmp_path / "state").exists()
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)
        assert runtime._repository._closed

    asyncio.run(scenario())


def test_internal_memory_worker_never_writes_private_canonical_database(tmp_path, monkeypatch):
    async def scenario():
        created = []
        observed_files = []
        observed_users = []
        original = LangGraphRuntime.create

        def capture(**kwargs):
            runtime = original(**kwargs)
            created.append((runtime, Path(kwargs["database_path"])))
            return runtime

        monkeypatch.setattr(LangGraphRuntime, "create", staticmethod(capture))

        class WorkerModel:
            async def stream(self, request):
                child, database = created[-1]
                observed_files.extend(
                    path.relative_to(database.parent).as_posix()
                    for path in database.parent.rglob("*")
                    if path.is_file()
                )
                observed_users.extend(
                    item
                    for item in await child._repository.load_items(child.thread_id)
                    if isinstance(item, UserMessageItem)
                )
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            '{"memory":"Result","memory_summary":"Result","skills":[]}',
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                raise AssertionError("borrowed worker model must remain parent-owned")

        result = await run_agent(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            model=WorkerModel(),
            sampled={},
            workspace_diff="",
            instructions="Consolidate the supplied evidence.",
            home_path=tmp_path / "home",
        )
        assert result and len(created) == 1 and observed_users
        # Inspect while the model is running, not only after temporary-directory deletion.
        assert observed_files == []

    asyncio.run(scenario())


def test_ephemeral_search_execution_compaction_and_cross_turn_context(tmp_path):
    async def scenario():
        calls = []

        class Tool:
            spec = ToolSpec(
                "calendar",
                "Create calendar meeting",
                {"type": "object"},
                exposure=ToolExposure.DEFERRED,
            )

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "calendar created once")

        class Model(AnswerModel):
            async def stream(self, request):
                self.requests.append(request)
                index = len(self.requests)
                names = {tool.name for tool in request.tools}
                turn, step = request.items[-1].turn_id, new_step_id()
                if index == 1:
                    assert "calendar" not in names and "tool_search" in names
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "calendar meeting"}
                    )
                elif index == 2:
                    assert "calendar" in names
                    assert any(isinstance(item, ToolResultItem) for item in request.items)
                    call = ToolCall(new_tool_call_id(), "calendar", {})
                elif index == 5:
                    # Compaction starts a fresh discovery window, just as in
                    # durable Runtime. The model can discover the tool again.
                    assert "calendar" not in names and "tool_search" in names
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "calendar"})
                else:
                    if index == 3:
                        assert any(
                            isinstance(item, ToolResultItem)
                            and item.content == "calendar created once"
                            for item in request.items
                        )
                    if index == 6:
                        assert "calendar" in names
                    text = (
                        "Retain calendar task and its completed result." if index == 4 else "done"
                    )
                    yield ModelCompleted((AssistantMessageItem(text, turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

        registry, model = ToolRegistry(), Model()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "state" / "history.db",
            home_path=tmp_path / "home",
            model=model,
            registry=registry,
            ephemeral=True,
        )
        try:
            assert isinstance(
                [event async for event in runtime.stream("schedule meeting")][-1], TurnCompleted
            )
            assert isinstance([event async for event in runtime.compact()][-1], TurnCompleted)
            assert isinstance(
                [event async for event in runtime.stream("continue calendar task")][-1],
                TurnCompleted,
            )
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(item, CompactionItem) for item in history) == 1
            assert len(calls) == 1 and len(model.requests) == 6
            assert not (tmp_path / "state").exists()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_ephemeral_memory_reads_remain_available_without_background_generation(tmp_path):
    async def scenario():
        root = tmp_path / "memories"
        root.mkdir()
        (root / "memory_summary.md").write_text("existing memory routing index")
        (root / "MEMORY.md").write_text("existing durable preference")

        class Model(AnswerModel):
            async def stream(self, request):
                self.requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(self.requests) == 1:
                    assert any(
                        "existing memory routing index" in getattr(item, "content", "")
                        for item in request.items
                    )
                    assert "memories::read" in {tool.name for tool in request.tools}
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(), "memories::read", {"path": "MEMORY.md"}
                                ),
                                turn,
                                step,
                            ),
                        )
                    )
                else:
                    assert any(
                        isinstance(item, ToolResultItem)
                        and "existing durable preference" in item.content
                        and not item.is_error
                        for item in request.items
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

        model = Model()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_use=True,
                memories_dedicated_tools=True,
            ),
            database_path=tmp_path / "state" / "history.db",
            home_path=tmp_path / "home",
            model=model,
            memory_root=root,
            ephemeral=True,
        )
        try:
            assert isinstance(
                [event async for event in runtime.stream("read memory")][-1], TurnCompleted
            )
            assert runtime._memory_service is None and runtime._memory_repository is None
            assert len(model.requests) == 2 and not (tmp_path / "state").exists()
            assert (root / "MEMORY.md").read_text() == "existing durable preference"
            for operation in (
                runtime.archive,
                runtime.unarchive,
                lambda: runtime.set_thread_memory_mode("disabled"),
            ):
                with pytest.raises(RuntimeError, match="ephemeral"):
                    await operation()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
