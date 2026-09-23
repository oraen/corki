"""Host reset clears generated state, not thread history or future eligibility."""

import asyncio
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import SQLiteMemoryRepository, git_baseline
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ContextItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall


class Main:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


@pytest.mark.parametrize("enabled", [True, False])
def test_runtime_reset_preserves_history_and_revokes_old_claim(tmp_path, enabled):
    async def scenario():
        home, root = tmp_path / "home", tmp_path / "custom-memory"
        database = tmp_path / "history.db"
        model = Main()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=enabled,
                memories_generate=False,
                memories_background_enabled=False,
            ),
            database_path=database,
            home_path=home,
            memory_root=root,
            model=model,
        )
        repository = SQLiteMemoryRepository(database)
        claim = await repository.claim_consolidation(lease_seconds=3600)
        assert claim is not None
        legacy = home / "memories_extensions"
        for directory in (root, legacy):
            (directory / "nested").mkdir(parents=True, exist_ok=True)
            (directory / "nested/old.md").write_text("old")
            (directory / ".hidden").write_text("private")
        (root / "memory_summary.md").write_text("v1\nOLD_ROUTING")
        git_baseline.prepare(root)
        old_object = git_baseline._git(root, "rev-parse", "HEAD:nested/old.md").strip().decode()
        assert (root / ".git/objects" / old_object[:2] / old_object[2:]).is_file()
        try:
            assert isinstance(
                [e async for e in runtime.stream("remember conversation")][-1], TurnCompleted
            )
            await repository.mark_thread_mode(runtime.thread_id, "disabled")
            before = await runtime._repository.load_items(runtime.thread_id)
            cleared = await runtime.reset_memory()
            assert set(cleared) == {root, legacy}
            assert all(
                directory.is_dir() and not list(directory.iterdir()) for directory in cleared
            )
            assert not (root / ".git").exists(), "reset must also erase old content objects"
            assert await runtime._repository.load_items(runtime.thread_id) == before
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT count(*) FROM memory_jobs").fetchone() == (0,)
                assert db.execute(
                    "SELECT memory_mode FROM threads WHERE id=?", (str(runtime.thread_id),)
                ).fetchone() == ("disabled",)
            called = []
            assert not await repository.complete_consolidation(
                claim, (), publish=lambda: called.append(True)
            )
            assert not called
            assert isinstance([e async for e in runtime.stream("continue")][-1], TurnCompleted)
            if enabled:
                updates = [
                    i
                    for i in model.requests[-1].items
                    if isinstance(i, ContextItem) and i.key == "memory.instructions"
                ]
                assert updates == [
                    i
                    for i in before
                    if isinstance(i, ContextItem) and i.key == "memory.instructions"
                ]
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                assert isinstance(
                    [e async for e in runtime.stream("new window")][-1], TurnCompleted
                )
                assert not any(
                    isinstance(i, ContextItem) and i.key == "memory.instructions"
                    for i in model.requests[-1].items
                )
                assert (await runtime._repository.load_items(runtime.thread_id))[
                    : len(before)
                ] == before
            assert await repository.claim_consolidation(lease_seconds=3600) is not None
        finally:
            await runtime.aclose()

        cold = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=enabled,
                memories_generate=False,
                memories_background_enabled=False,
            ),
            database_path=database,
            home_path=home,
            memory_root=root,
            model=model,
            thread_id=runtime.thread_id,
        )
        try:
            assert isinstance([e async for e in cold.stream("after restart")][-1], TurnCompleted)
            assert not (root / "memory_summary.md").exists()
            updates = [
                i
                for i in model.requests[-1].items
                if isinstance(i, ContextItem) and i.key == "memory.instructions"
            ]
            assert not updates or updates[-1].snapshot_content == ""
            cold_items = await cold._repository.load_items(cold.thread_id)
            assert all(item in cold_items for item in before)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("new_owner", [False, True])
@pytest.mark.parametrize("late_tool", [False, "completed", "item"])
def test_reset_while_consolidator_is_sampling_prevents_late_publication(
    tmp_path, new_owner, late_tool
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Memory(Main):
            async def stream(self, request):
                self.requests.append(request)
                entered.set()
                await release.wait()
                if late_tool and len(self.requests) == 1:
                    item = ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "apply_patch",
                            {
                                "patch": "*** Begin Patch\n*** Add File: STALE_WRITE_PROOF.md\n"
                                "+stale worker wrote after reset\n*** End Patch"
                            },
                        ),
                        request.items[-1].turn_id,
                        new_step_id(),
                    )
                    if late_tool == "item":
                        yield ModelItemCompleted(item)
                    yield ModelCompleted((item,))
                    return
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps({"memory": "late", "memory_summary": "late", "skills": []}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

        root = tmp_path / "memories"
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            memory_root=root,
            model=Main(),
            memory_model=Memory(),
        )
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            await asyncio.wait_for(entered.wait(), 2)
            await runtime.reset_memory()
            assert not list(root.iterdir())
            published = None
            if new_owner:

                class Fresh(Main):
                    async def stream(self, request):
                        yield ModelCompleted(
                            (
                                AssistantMessageItem(
                                    json.dumps(
                                        {
                                            "memory": "NEW_OWNER_MEMORY",
                                            "memory_summary": "NEW_OWNER_SUMMARY",
                                            "skills": [],
                                        }
                                    ),
                                    request.items[-1].turn_id,
                                    new_step_id(),
                                ),
                            )
                        )

                fresh = await LangGraphRuntime.acreate(
                    settings=CorkiSettings(
                        working_directory=tmp_path, skills_enabled=False, memories_enabled=True
                    ),
                    database_path=tmp_path / "history.db",
                    home_path=tmp_path / "home",
                    memory_root=root,
                    model=Main(),
                    memory_model=Fresh(),
                )
                try:
                    assert isinstance(
                        [e async for e in fresh.stream("new work")][-1], TurnCompleted
                    )
                    report = await asyncio.wait_for(fresh._memory_service.wait(), 3)
                    assert report.consolidated and not report.failed
                    published = (
                        (root / "MEMORY.md").read_bytes(),
                        (root / "memory_summary.md").read_bytes(),
                        git_baseline.read(root),
                    )
                    assert b"NEW_OWNER_MEMORY" in published[0]
                finally:
                    await fresh.aclose()
            release.set()
            report = await asyncio.wait_for(runtime._memory_service.wait(), 2)
            assert report.failed == 1
            assert not (root / "STALE_WRITE_PROOF.md").exists()
            if new_owner:
                assert published == (
                    (root / "MEMORY.md").read_bytes(),
                    (root / "memory_summary.md").read_bytes(),
                    git_baseline.read(root),
                )
            else:
                assert not (root / "MEMORY.md").exists()
                assert not (root / ".git").exists()
            with sqlite3.connect(tmp_path / "history.db") as db:
                if new_owner:
                    assert db.execute(
                        "SELECT status,error FROM memory_jobs "
                        "WHERE kind='memory_consolidate_global'"
                    ).fetchall() == [("succeeded", None)]
                else:
                    assert db.execute("SELECT count(*) FROM memory_jobs").fetchone() == (0,)
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_close_waiter", [False, True])
def test_cancelled_reset_and_runtime_close_join_actual_file_removal(
    tmp_path, monkeypatch, cancel_close_waiter
):
    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        root = tmp_path / "memories"
        root.mkdir()
        target = root / "old"
        target.write_text("old")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            memory_root=root,
            model=Main(),
        )
        await runtime._ensure_ready()
        downstream_closed = []
        for name, service, method in (
            ("model", runtime._model, "aclose"),
            ("repository", runtime._repository, "close"),
            ("writer", runtime._writer, "aclose"),
        ):
            original = getattr(service, method)

            async def close(name=name, original=original):
                assert not target.exists(), "dependencies closed before reset finished"
                downstream_closed.append(name)
                await original()

            monkeypatch.setattr(service, method, close)
        loop, unlink = asyncio.get_running_loop(), Path.unlink

        def remove(path, *args, **kwargs):
            if path == target:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", remove)
        reset = asyncio.create_task(runtime.reset_memory())
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            reset.cancel()
            await asyncio.sleep(0)
            reset.cancel()
            closing = asyncio.create_task(runtime.aclose())
            done, _ = await asyncio.wait((reset, closing), timeout=0.03)
            assert not done and target.exists()
            assert not downstream_closed and runtime._writer.held
            shared_close = runtime._close_task
            if cancel_close_waiter:
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing
                assert not shared_close.done() and not shared_close.cancelling()
                closing = asyncio.create_task(runtime.aclose())
                done, _ = await asyncio.wait((closing,), timeout=0.03)
                assert not done and runtime._close_task is shared_close
                assert not downstream_closed and target.exists()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await reset
            if closing is not None:
                await closing
            await runtime.aclose()
        assert not target.exists()
        assert downstream_closed == ["model", "repository", "writer"]
        assert not runtime._writer.held
        with pytest.raises(RuntimeError, match="closed"):
            await runtime.reset_memory()

    asyncio.run(scenario())
