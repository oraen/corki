"""Batch admission is immutable and reads cannot manufacture execution claims."""

import asyncio
import threading

import pytest

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError
from corki.storage.volatile import VolatileSessionRepository


@pytest.mark.parametrize("volatile", [False, True])
def test_batch_snapshot_is_immutable_scoped_and_survives_reopen(tmp_path, volatile):
    async def scenario():
        path = tmp_path / "batch.db"
        repository = VolatileSessionRepository() if volatile else SQLiteSessionRepository(path)
        thread, turn = new_thread_id(), new_turn_id()
        key = f"stop:{turn}:1:"
        snapshot = {"version": 1, "commands": [{"key": "one"}, {"key": "two"}]}
        try:
            await repository.create_thread(thread, tmp_path)
            assert await repository.load_hook_batch(thread, turn, key) is None
            await repository.save_hook_batch(thread, turn, key, snapshot)
            await repository.save_hook_batch(thread, turn, key, snapshot)
            assert await repository.load_hook_batch(thread, turn, key) == (snapshot, {})
            with pytest.raises(StorageIntegrityError, match="identity collision"):
                await repository.save_hook_batch(thread, turn, key, {"commands": []})
            with pytest.raises(StorageIntegrityError, match="identity collision"):
                await repository.load_hook_batch(new_thread_id(), turn, key)
            with pytest.raises(StorageIntegrityError, match="identity collision"):
                await repository.load_hook_batch(thread, new_turn_id(), key)
            assert await repository.load_hook_batch(thread, turn, f"stop:{turn}:2:") is None
            request, result = {"command": "one"}, {"stdout": "{}", "exit_code": 0}
            assert (
                await repository.claim_hook_execution(thread, turn, key + "file:one", request)
                is None
            )
            await repository.complete_hook_execution(
                thread, turn, key + "file:one", request, result
            )
            if not volatile:
                await repository.close()
                repository = SQLiteSessionRepository(path)
            assert await repository.load_hook_batch(thread, turn, key) == (
                snapshot,
                {key + "file:one": {"request": request, "result": result}},
            )
            assert not await repository.has_hook_executions(thread, turn, key + "file:two")
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_legacy_claim_without_batch_is_not_guessed_into_a_new_batch(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "legacy.db")
        thread, turn = new_thread_id(), new_turn_id()
        key = f"stop:{turn}:1:"
        try:
            await repository.create_thread(thread, tmp_path)
            await repository.claim_hook_execution(thread, turn, key + "file:one", {})
            with pytest.raises(StorageIntegrityError, match="no batch snapshot"):
                await repository.load_hook_batch(thread, turn, key)
            with pytest.raises(RuntimeError, match="unknown"):
                await repository.claim_hook_execution(thread, turn, key + "file:one", {})
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("volatile", [False, True])
def test_cancelled_batch_write_joins_owned_worker(tmp_path, monkeypatch, volatile):
    async def scenario():
        repository = (
            VolatileSessionRepository()
            if volatile
            else SQLiteSessionRepository(tmp_path / "owned.db")
        )
        thread, turn = new_thread_id(), new_turn_id()
        key = f"stop:{turn}:1:"
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = repository._save_hook_batch

        def held(*args):
            original(*args)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)

        task = None
        try:
            await repository.create_thread(thread, tmp_path)
            monkeypatch.setattr(repository, "_save_hook_batch", held)
            task = asyncio.create_task(
                repository.save_hook_batch(thread, turn, key, {"version": 1})
            )
            await asyncio.wait_for(entered.wait(), 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await repository.load_hook_batch(thread, turn, key) == ({"version": 1}, {})
            assert not await repository.has_hook_executions(thread, turn, key)
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await repository.close()

    asyncio.run(scenario())
