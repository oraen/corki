"""Expiry uses the actual SQLite workspace fence, including cancellation joins."""

import asyncio
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from corki.memory import SQLiteMemoryRepository
from corki.memory.artifacts import sync_stage_one_artifacts
from corki.storage import SQLiteSessionRepository


def _resource(root):
    resource = root / "extensions/team/resources/2001-01-01T00-00-00-old.md"
    resource.parent.mkdir(parents=True)
    (resource.parent.parent / "instructions.md").write_text("keep")
    resource.write_text("old")
    return resource


def test_stale_claim_cannot_prune_memory_inputs(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        SQLiteSessionRepository(database)
        repository = SQLiteMemoryRepository(database)
        claim = await repository.claim_consolidation(lease_seconds=3600)
        assert claim is not None
        root = tmp_path / "memories"
        resource = _resource(root)
        assert not await repository.write_consolidation_workspace(
            replace(claim, ownership_token="stale"), lambda: sync_stage_one_artifacts(root, ())
        )
        assert resource.read_text() == "old"
        assert await repository.write_consolidation_workspace(
            claim, lambda: sync_stage_one_artifacts(root, ())
        )
        assert not resource.exists()

    asyncio.run(scenario())


def test_cancellation_joins_pruning_before_releasing_workspace_lock(tmp_path, monkeypatch):
    async def scenario():
        database = tmp_path / "history.db"
        SQLiteSessionRepository(database)
        repository = SQLiteMemoryRepository(database)
        claim = await repository.claim_consolidation(lease_seconds=3600)
        assert claim is not None
        root = tmp_path / "memories"
        resource = _resource(root)
        started, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        unlink = Path.unlink

        def remove(path, *args, **kwargs):
            if path == resource:
                loop.call_soon_threadsafe(started.set)
                assert release.wait(5), "test failed to release owned deletion"
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", remove)
        pruning = asyncio.create_task(
            repository.write_consolidation_workspace(
                claim, lambda: sync_stage_one_artifacts(root, ())
            )
        )
        contender = None
        try:
            await asyncio.wait_for(started.wait(), 1)
            pruning.cancel()
            await asyncio.sleep(0)
            pruning.cancel()
            contender = asyncio.create_task(
                repository.heartbeat_consolidation(claim, lease_seconds=3600)
            )
            done, _ = await asyncio.wait((pruning, contender), timeout=0.03)
            assert not done, "cancelled pruning must retain its SQLite write fence"
            assert resource.exists()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pruning
            if contender is not None:
                assert await contender
        assert not resource.exists()

    asyncio.run(scenario())
