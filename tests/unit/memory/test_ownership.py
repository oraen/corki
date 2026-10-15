"""Global memory lease fencing, source snapshots and stream ownership."""

import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from corki.config import CorkiSettings
from corki.memory import (
    LocalMemoryBackend,
    LongTermMemoryService,
    MemoryExtractionClaim,
    SQLiteMemoryRepository,
    git_baseline,
)
from corki.memory.permissions import MemoryPermissionSnapshot
from corki.models import ModelCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


def _service(tmp_path, model, repository_type=SQLiteMemoryRepository):
    database, root = tmp_path / "session.db", tmp_path / "memories"
    SQLiteSessionRepository(database)
    repository = repository_type(database)
    LocalMemoryBackend(root).add_note(
        "2026-09-06T10-00-00-preference.md", "Prefer concise explanations."
    )
    service = LongTermMemoryService(
        settings=CorkiSettings(
            working_directory=tmp_path,
            memories_enabled=True,
            memories_lease_seconds=1,
        ),
        repository=repository,
        model=model,
        root=root,
    )
    return service, repository, database, root


def _completion(request, text="old owner memory"):
    return ModelCompleted(
        (
            AssistantMessageItem(
                json.dumps({"memory": text, "memory_summary": text, "skills": []}),
                request.items[-1].turn_id,
                new_step_id(),
            ),
        )
    )


def test_stale_owner_does_not_overwrite_new_owner_artifacts(tmp_path):
    async def scenario():
        class Model:
            async def stream(self, request):
                with sqlite3.connect(database) as connection:
                    connection.execute("UPDATE memory_jobs SET lease_until=0")
                replacement = await repository.claim_consolidation(lease_seconds=60)
                assert replacement is not None
                (root / "MEMORY.md").write_text("NEW OWNER", encoding="utf-8")
                (root / "memory_summary.md").write_text("NEW SUMMARY", encoding="utf-8")
                yield _completion(request)

        service, repository, database, root = _service(tmp_path, Model())
        report = await service.run_once(new_thread_id())
        assert report.failed == 1
        assert not report.consolidated
        assert (root / "MEMORY.md").read_text() == "NEW OWNER"
        assert (root / "memory_summary.md").read_text() == "NEW SUMMARY"
        assert "MEMORY.md" not in git_baseline.read(root)

    asyncio.run(scenario())


def test_stale_owner_cannot_prepare_new_owners_workspace(tmp_path):
    async def scenario():
        service, repository, _, root = _service(tmp_path, object())
        git_baseline.prepare(root)
        old = await repository.claim_consolidation(lease_seconds=0)
        new = await repository.claim_consolidation(lease_seconds=60)
        assert old is not None and new is not None and old != new
        diff = root / "phase2_workspace_diff.md"
        diff.write_text("NEW OWNER WORKSPACE DIFF", encoding="utf-8")
        try:
            with pytest.raises(RuntimeError, match="ownership lost"):
                await service._consolidation_work(
                    old, MemoryPermissionSnapshot(service._settings.execution_permissions)
                )
            assert diff.read_text() == "NEW OWNER WORKSPACE DIFF"
            assert await repository.heartbeat_consolidation(new, lease_seconds=60)
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("prepare_fails", [False, True])
def test_cancelled_preparation_holds_fence_until_writer_finishes(
    tmp_path, monkeypatch, memory_worker_state_dirs, prepare_fails
):
    async def scenario():
        service, repository, _, root = _service(tmp_path, object())
        claim = await repository.claim_consolidation(lease_seconds=0)
        assert claim is not None
        entered, contender_entered = asyncio.Event(), asyncio.Event()
        release, finished = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original_prepare = git_baseline.prepare
        original_claim = repository._claim_consolidation

        def held_prepare(path):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(5), "test did not release baseline preparation"
                original_prepare(path)
                if prepare_fails:
                    raise OSError("preparation failed after filesystem work")
            finally:
                finished.set()

        def competing_claim(seconds):
            loop.call_soon_threadsafe(contender_entered.set)
            result = original_claim(seconds)
            assert finished.is_set(), "takeover overtook the owned filesystem writer"
            return result

        monkeypatch.setattr(git_baseline, "prepare", held_prepare)
        monkeypatch.setattr(repository, "_claim_consolidation", competing_claim)
        work = asyncio.create_task(
            service._consolidation_work(
                claim, MemoryPermissionSnapshot(service._settings.execution_permissions)
            )
        )
        contender = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            work.cancel()
            await asyncio.sleep(0)
            work.cancel()
            contender = asyncio.create_task(repository.claim_consolidation(lease_seconds=60))
            await asyncio.wait_for(contender_entered.wait(), 3)
            done, _ = await asyncio.wait((work, contender), timeout=0.03)
            assert not done and not finished.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await work
            if contender is not None:
                replacement = await contender
                assert replacement is not None and replacement != claim
                assert await repository.heartbeat_consolidation(replacement, lease_seconds=60)
            await service.aclose()
        assert finished.is_set()
        assert memory_worker_state_dirs and all(not p.exists() for p in memory_worker_state_dirs)
        assert (root / ".git").is_dir(), "owned memory artifacts must survive temporary cleanup"

    asyncio.run(scenario())


@pytest.mark.parametrize("slow_layout", [False, True])
def test_memory_completed_is_terminal_and_closes_stream(tmp_path, monkeypatch, slow_layout):
    async def scenario():
        started, closed = asyncio.Event(), asyncio.Event()
        if slow_layout:
            original_layout = git_baseline.ensure_layout

            def delayed_layout(root):
                # Reproduce filesystem preparation exceeding the old whole-pass watchdog.
                threading.Event().wait(1.1)
                return original_layout(root)

            monkeypatch.setattr(git_baseline, "ensure_layout", delayed_layout)

        class Model:
            closed = False

            async def stream(self, request):
                started.set()
                try:
                    yield _completion(request)
                    raise AssertionError("consumer read beyond ModelCompleted")
                finally:
                    self.closed = True
                    closed.set()

        model = Model()
        service, _, _, _ = _service(tmp_path, model)
        work = asyncio.create_task(service.run_once(new_thread_id()))
        try:
            # Preparation/publication include real Git and SQLite work. The one-second
            # stream-closure watchdog starts only once the model is actually running.
            await asyncio.wait_for(started.wait(), timeout=10)
            await asyncio.wait_for(closed.wait(), timeout=1)
            report = await asyncio.wait_for(work, timeout=10)
            assert report.consolidated
            assert model.closed
        finally:
            if not work.done():
                work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["lost", "error"])
@pytest.mark.parametrize("slow_prepare", [False, True])
def test_failed_heartbeat_stops_model_and_does_not_publish(
    tmp_path, monkeypatch, failure, slow_prepare
):
    async def scenario():
        started, heartbeat_failed, closed = (asyncio.Event() for _ in range(3))
        if slow_prepare:
            original_layout = git_baseline.ensure_layout

            def delayed_layout(root):
                threading.Event().wait(1.1)
                return original_layout(root)

            monkeypatch.setattr(git_baseline, "ensure_layout", delayed_layout)

        class Repository(SQLiteMemoryRepository):
            async def heartbeat_consolidation(self, claim, *, lease_seconds):
                await started.wait()
                heartbeat_failed.set()
                if failure == "error":
                    raise OSError("heartbeat storage failure")
                return False

        class Model:
            closed = False

            async def stream(self, request):
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield _completion(request)
                finally:
                    self.closed = True
                    closed.set()

        model = Model()
        service, _, _, root = _service(tmp_path, model, Repository)
        work = asyncio.create_task(service.run_once(new_thread_id()))
        try:
            # Git/SQLite preparation is not the heartbeat-to-model-stop interval.
            # Keep the one-second stop watchdog, measured from the actual fault.
            await asyncio.wait_for(heartbeat_failed.wait(), timeout=10)
            await asyncio.wait_for(closed.wait(), timeout=1)
            report = await asyncio.wait_for(work, timeout=10)
            assert report.failed == 1
            assert model.closed
            assert not (root / "MEMORY.md").exists()
            assert service.warnings
        finally:
            if not work.done():
                work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            await service.aclose()

    asyncio.run(scenario())


def test_heartbeat_is_owner_scoped_and_extends_lease(tmp_path):
    async def scenario():
        _, repository, database, _ = _service(tmp_path, object())
        await repository.enqueue_consolidation(force=True)
        old = await repository.claim_consolidation(lease_seconds=0)
        assert old is not None
        assert await repository.heartbeat_consolidation(old, lease_seconds=60)
        assert await repository.claim_consolidation(lease_seconds=60) is None
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE memory_jobs SET lease_until=0")
        new = await repository.claim_consolidation(lease_seconds=60)
        assert new is not None and old != new
        assert not await repository.heartbeat_consolidation(old, lease_seconds=60)
        assert await repository.heartbeat_consolidation(new, lease_seconds=60)

    asyncio.run(scenario())


def test_running_consolidation_renews_more_than_once(tmp_path):
    async def scenario():
        renewed = asyncio.Event()

        class Repository(SQLiteMemoryRepository):
            beats = 0

            async def heartbeat_consolidation(self, claim, *, lease_seconds):
                result = await super().heartbeat_consolidation(claim, lease_seconds=lease_seconds)
                self.beats += 1
                if self.beats >= 2:
                    renewed.set()
                return result

        class Model:
            async def stream(self, request):
                await renewed.wait()
                yield _completion(request)

        service, repository, _, _ = _service(tmp_path, Model(), Repository)
        report = await asyncio.wait_for(service.run_once(new_thread_id()), timeout=2)
        assert report.consolidated
        assert repository.beats >= 2
        assert not service.warnings

    asyncio.run(scenario())


def test_consolidation_marks_only_the_exact_selected_source_version(tmp_path):
    async def scenario():
        _, repository, database, _ = _service(tmp_path, object())
        thread = new_thread_id()
        await SQLiteSessionRepository(database).create_thread(thread, tmp_path)
        old_version = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        new_version = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO memory_stage1_outputs "
                "(thread_id, source_updated_at, cwd, raw_memory, rollout_summary) "
                "VALUES (?, ?, ?, 'old', 'old')",
                (str(thread), old_version, str(tmp_path)),
            )
        await repository.enqueue_consolidation()
        claim = await repository.claim_consolidation(lease_seconds=60)
        assert claim is not None
        selected = await repository.load_consolidation_inputs(limit=10, max_unused_days=30)
        assert len(selected) == 1 and selected[0].source_updated_at == old_version
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE memory_stage1_outputs SET source_updated_at=?", (new_version,)
            )
        assert await repository.complete_consolidation(claim, selected)
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT selected_for_phase2, selected_source_updated_at FROM memory_stage1_outputs"
            ).fetchone()
        assert row == (0, None), "the newer snapshot was never consumed by this consolidation"

    asyncio.run(scenario())


def test_publish_is_fenced_against_takeover_and_cancellation_joins_writer(tmp_path):
    async def scenario():
        _, repository, _, _ = _service(tmp_path, object())
        await repository.enqueue_consolidation(force=True)
        claim = await repository.claim_consolidation(lease_seconds=0)
        assert claim is not None
        entered, release = threading.Event(), threading.Event()

        def publish():
            entered.set()
            assert release.wait(timeout=3)

        writing = asyncio.create_task(repository.complete_consolidation(claim, (), publish=publish))
        takeover = None
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            takeover = asyncio.create_task(repository.claim_consolidation(lease_seconds=60))
            writing.cancel()
            await asyncio.sleep(0.03)
            assert not writing.done(), "cancellation must wait for the actual writer"
            assert not takeover.done(), "ownership cannot change during publication"
        finally:
            release.set()
            await asyncio.gather(writing, return_exceptions=True)
            if takeover is not None:
                assert await takeover is None
        assert writing.cancelled()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_extraction_failure_recording_does_not_leak_or_mask_cancellation(tmp_path, cancel):
    async def scenario():
        started, failed, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        first, second = new_thread_id(), new_thread_id()

        class Repository(SQLiteMemoryRepository):
            async def claim_extraction_jobs(self, **kwargs):
                return tuple(
                    MemoryExtractionClaim(thread, tmp_path, "version", str(thread), ())
                    for thread in (first, second)
                )

            async def complete_extraction(self, claim, memory):
                return True

            async def fail_extraction(self, claim, error, *, retry_delay_seconds):
                failed.set()
                raise OSError("cannot persist extraction failure")

            async def claim_consolidation(self, **kwargs):
                return None

        class Model:
            closed = 0

            async def stream(self, request):
                try:
                    if str(first) in request.items[0].content:
                        await started.wait()
                        if cancel:
                            await asyncio.Event().wait()
                        raise ValueError("invalid extraction response")
                    started.set()
                    await release.wait()
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                json.dumps(
                                    {"raw_memory": "", "rollout_summary": "", "rollout_slug": None}
                                ),
                                request.items[0].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                finally:
                    self.closed += 1

        model = Model()
        service, _, _, _ = _service(tmp_path, model, Repository)
        running = asyncio.create_task(service.run_once(new_thread_id()))
        try:
            await asyncio.wait_for(started.wait(), 1)
            if cancel:
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            else:
                await asyncio.wait_for(failed.wait(), 1)
                release.set()
                report = await running
                assert report.failed == 1 and report.empty == 1
            assert model.closed == 2
            assert any("cannot persist extraction failure" in item for item in service.warnings)
        finally:
            release.set()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())
