"""Map Codex workspace-diff and artifact-validation gates to the local pipeline."""

import asyncio
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.memory import (
    LocalMemoryBackend,
    LongTermMemoryService,
    SQLiteMemoryRepository,
    git_baseline,
    workspace,
)
from corki.models import ModelCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


class Model:
    def __init__(self):
        self.requests = []
        self.before_completion = lambda: None
        self.snapshots = []

    async def stream(self, request):
        self.requests.append(request)
        env = next(i for i in request.items if getattr(i, "key", None) == "environment.primary")
        root = Path(re.search(r"<cwd>(.*?)</cwd>", env.content, re.S)[1])
        self.snapshots.append(workspace.capture(root))
        self.before_completion()
        yield ModelCompleted(
            (
                AssistantMessageItem(
                    json.dumps(
                        {
                            "memory": f"memory {len(self.requests)}",
                            "memory_summary": "summary",
                            "skills": [
                                {"name": "verify", "description": "Verify", "content": "Run pytest"}
                            ],
                        }
                    ),
                    request.items[-1].turn_id,
                    new_step_id(),
                ),
            )
        )


def _setup(tmp_path):
    database, root = tmp_path / "sessions.db", tmp_path / "memories"
    SQLiteSessionRepository(database)
    LocalMemoryBackend(root).add_note("2026-09-06T10-00-00-verify.md", "Use pytest")
    model = Model()
    repository = SQLiteMemoryRepository(database)
    service = LongTermMemoryService(
        settings=CorkiSettings(working_directory=tmp_path, memories_enabled=True),
        repository=repository,
        model=model,
        root=root,
    )
    return service, model, root, repository


def _elapse_success_cooldown(workspace):
    """Artifact tests run the next eligible pass; dedicated policy tests cover the six-hour gate."""
    with sqlite3.connect(workspace / "sessions.db") as connection:
        connection.execute(
            "UPDATE memory_jobs SET finished_at=0 WHERE kind='memory_consolidate_global'"
        )


@pytest.mark.parametrize("change", ["detail", "summary", "skill", "delete_skill", "legacy"])
def test_output_changes_and_legacy_baselines_do_not_skip_consolidation(tmp_path, change):
    async def scenario():
        service, model, root, _ = _setup(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidated
        if change == "detail":
            (root / "MEMORY.md").write_text("v1\n\nEdited detail", encoding="utf-8")
        elif change == "summary":
            (root / "memory_summary.md").write_text("outdated\n", encoding="utf-8")
        elif change == "skill":
            (root / "skills/verify/SKILL.md").write_text("Edited procedure", encoding="utf-8")
        elif change == "delete_skill":
            (root / "skills/verify/SKILL.md").unlink()
        else:
            path = root / ".consolidation-baseline.json"
            # Recreate an actual old-version store; migration tests separately
            # prove v3 content survives and unknown versions force a model pass.
            shutil.rmtree(root / ".git")  # exclusively owned fixture metadata
            path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        _elapse_success_cooldown(tmp_path)
        report = await service.run_once(new_thread_id())
        assert report.consolidated and not report.consolidation_skipped
        assert len(model.requests) == 2
        if change == "skill":
            assert (
                workspace.text(model.snapshots[-1], "skills/verify/SKILL.md") == "Edited procedure"
            )
        _elapse_success_cooldown(tmp_path)
        third = await service.run_once(new_thread_id())
        assert third.consolidation_skipped
        assert len(model.requests) == 2

    asyncio.run(scenario())


def test_symlinked_output_is_not_accepted_as_unchanged_or_read_into_model(tmp_path):
    async def scenario():
        service, model, root, _ = _setup(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidated
        outside = tmp_path / "outside.md"
        outside.write_text("EXTERNAL SECRET", encoding="utf-8")
        path = root / "MEMORY.md"
        path.unlink()
        path.symlink_to(outside)
        _elapse_success_cooldown(tmp_path)
        report = await service.run_once(new_thread_id())
        assert report.consolidated and not report.failed
        assert len(model.requests) == 2
        assert outside.read_text() == "EXTERNAL SECRET"
        assert not path.is_symlink(), "startup removes the link without following its target"
        assert "EXTERNAL SECRET" not in workspace.text(model.snapshots[-1], "MEMORY.md")

    asyncio.run(scenario())


def test_invalid_published_artifact_does_not_advance_success_baseline(tmp_path, monkeypatch):
    from corki.memory import pipeline

    async def scenario():
        service, _, root, repository = _setup(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidated
        baseline = git_baseline.read(root)
        LocalMemoryBackend(root).add_note("2026-09-06T11-00-00-change.md", "A new input")
        write = pipeline.write_consolidated_artifacts

        def invalid_write(root, value):
            write(root, value)
            (root / "memory_summary.md").write_text("not v1", encoding="utf-8")

        monkeypatch.setattr(pipeline, "write_consolidated_artifacts", invalid_write)
        _elapse_success_cooldown(tmp_path)
        report = await service.run_once(new_thread_id())
        assert report.failed == 1 and not report.consolidated
        assert git_baseline.read(root) == baseline
        assert await repository.claim_consolidation(lease_seconds=60) is None  # retry cooldown

    asyncio.run(scenario())


def test_partial_publication_failure_keeps_watermark_pending_and_retry_rebuilds(
    tmp_path, monkeypatch
):
    from corki.memory import artifacts

    async def scenario():
        service, model, root, _ = _setup(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidated
        baseline = git_baseline.read(root)
        with sqlite3.connect(tmp_path / "sessions.db") as connection:
            watermark = connection.execute(
                "SELECT completed_watermark FROM memory_jobs WHERE job_key='global'"
            ).fetchone()[0]
        LocalMemoryBackend(root).add_note("2026-09-06T11-00-00-change.md", "A new input")
        write = artifacts._atomic_write

        def fail_summary(path, content):
            if path.name == "memory_summary.md":
                raise OSError("injected partial publication")
            write(path, content)

        with monkeypatch.context() as patch:
            patch.setattr(artifacts, "_atomic_write", fail_summary)
            _elapse_success_cooldown(tmp_path)
            report = await service.run_once(new_thread_id())
        assert report.failed == 1 and not report.consolidated
        assert "memory 2" in (root / "MEMORY.md").read_text()  # per-file, not set-atomic
        assert git_baseline.read(root) == baseline
        with sqlite3.connect(tmp_path / "sessions.db") as connection:
            assert connection.execute(
                "SELECT completed_watermark, status FROM memory_jobs WHERE job_key='global'"
            ).fetchone() == (watermark, "failed")
            connection.execute("UPDATE memory_jobs SET retry_at=0 WHERE job_key='global'")
        retry = await service.run_once(new_thread_id())
        assert retry.consolidated and not retry.consolidation_skipped
        assert "memory 3" in (root / "MEMORY.md").read_text()
        assert len(model.requests) == 3
        _elapse_success_cooldown(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidation_skipped

    asyncio.run(scenario())


def test_success_baselines_current_shared_tree_including_late_file_writes(tmp_path):
    async def scenario():
        service, model, root, _ = _setup(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidated
        LocalMemoryBackend(root).add_note("2026-09-06T11-00-00-change.md", "Start another pass")

        def concurrent_note():
            if len(model.requests) == 2:
                LocalMemoryBackend(root).add_note(
                    "2026-09-06T12-00-00-late.md", "Late constraint not sampled yet"
                )

        model.before_completion = concurrent_note
        _elapse_success_cooldown(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidated
        assert all("Late constraint" not in i.content for i in model.requests[-1].items)
        assert "extensions/ad_hoc/notes/2026-09-06T12-00-00-late.md" not in model.snapshots[-1]
        _elapse_success_cooldown(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidation_skipped
        assert "Late constraint" in workspace.text(
            git_baseline.read(root), "extensions/ad_hoc/notes/2026-09-06T12-00-00-late.md"
        )
        assert len(model.requests) == 2
        # Native reset snapshots current files; this is not evidence that the
        # model read them. DB source-version accounting remains independent.

    asyncio.run(scenario())


def test_symlinked_raw_input_is_removed_before_sync_without_reading_target(tmp_path):
    async def scenario():
        service, model, root, _ = _setup(tmp_path)
        outside = tmp_path / "outside.md"
        outside.write_text("EXTERNAL SECRET", encoding="utf-8")
        path = root / "raw_memories.md"
        path.symlink_to(outside)
        report = await service.run_once(new_thread_id())
        assert report.consolidated and not report.failed
        assert len(model.requests) == 1
        assert not path.is_symlink() and outside.read_text() == "EXTERNAL SECRET"
        assert "EXTERNAL SECRET" not in workspace.text(model.snapshots[-1], "raw_memories.md")

    asyncio.run(scenario())


def test_process_exit_after_baseline_before_db_commit_recovers_without_resampling(tmp_path):
    async def scenario():
        service, model, root, _ = _setup(tmp_path)
        assert (await service.run_once(new_thread_id())).consolidated
        with sqlite3.connect(tmp_path / "sessions.db") as connection:
            old_watermark = connection.execute(
                "SELECT completed_watermark FROM memory_jobs WHERE job_key='global'"
            ).fetchone()[0]
        LocalMemoryBackend(root).add_note("2026-09-06T11-00-00-change.md", "Crash recovery input")
        _elapse_success_cooldown(tmp_path)
        code = """
import asyncio, json, os, sys
from pathlib import Path
from corki.config import CorkiSettings
from corki.memory import LongTermMemoryService, SQLiteMemoryRepository, git_baseline
from corki.models import ModelCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import AssistantMessageItem, new_step_id

workspace = Path(sys.argv[1])
class Model:
    async def stream(self, request):
        text = json.dumps({'memory': 'after-crash', 'memory_summary': 'after-crash', 'skills': []})
        item = AssistantMessageItem(text, request.items[-1].turn_id, new_step_id())
        yield ModelCompleted((item,))

original = git_baseline.reset
def crash_after_baseline(root, **kwargs):
    original(root, **kwargs)
    os._exit(23)
git_baseline.reset = crash_after_baseline
service = LongTermMemoryService(
    settings=CorkiSettings(working_directory=workspace, memories_enabled=True),
    repository=SQLiteMemoryRepository(workspace / 'sessions.db'),
    model=Model(), root=workspace / 'memories',
)
asyncio.run(service.run_once(new_thread_id()))
"""
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 23, result.stderr
        assert "after-crash" in (root / "MEMORY.md").read_text()
        with sqlite3.connect(tmp_path / "sessions.db") as connection:
            assert connection.execute(
                "SELECT completed_watermark, status FROM memory_jobs WHERE job_key='global'"
            ).fetchone() == (old_watermark, "running")
            connection.execute("UPDATE memory_jobs SET lease_until=0 WHERE job_key='global'")
        resumed = await service.run_once(new_thread_id())
        assert resumed.consolidation_skipped and not resumed.consolidated
        assert len(model.requests) == 1, "the child's completed model result must not be resampled"
        with sqlite3.connect(tmp_path / "sessions.db") as connection:
            completed, status = connection.execute(
                "SELECT completed_watermark, status FROM memory_jobs WHERE job_key='global'"
            ).fetchone()
        # File-only changes do not enqueue a fictitious DB input watermark.
        assert completed == old_watermark and status == "succeeded"

    asyncio.run(scenario())
