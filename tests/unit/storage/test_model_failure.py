"""Failure facts are immutable and their owned writes preserve cancellation."""

import asyncio
import json
import sqlite3
import threading
from dataclasses import replace

import pytest

from corki.models import ModelCompleted
from corki.models.failure import ModelFailure
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError


def test_failure_records_preserve_budget_and_reject_conflicting_rewrites(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        failure = ModelFailure("disconnected", "transport", True, 2)
        await repository.save_model_failure(thread, turn, 3, failure)
        await repository.save_model_failure(thread, turn, 3, failure)
        with pytest.raises(StorageIntegrityError):
            await repository.save_model_failure(thread, turn, 3, replace(failure, retries_used=0))
        reopened = SQLiteSessionRepository(repository.path)
        assert await reopened.load_model_failure(thread, turn, 3) == failure
        assert await reopened.load_model_step(thread, turn, 3) is None

    asyncio.run(scenario())


def test_legacy_failure_default_counter_allows_idempotent_commit(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        payload = {"message": "lost", "kind": "transport", "retryable": True, "retries_used": 2}
        with sqlite3.connect(repository.path) as connection:
            connection.execute(
                "INSERT INTO model_failures VALUES (?, ?, ?, ?)",
                (thread, turn, 0, json.dumps(payload)),
            )
        failure = await repository.load_model_failure(thread, turn, 0)
        assert failure.connection_retries_used == 0
        await repository.save_model_failure(thread, turn, 0, failure)
        with sqlite3.connect(repository.path) as connection:
            row = connection.execute("SELECT payload_json FROM model_failures").fetchone()
            assert json.loads(row[0]) == payload

    asyncio.run(scenario())


def test_legacy_payment_failure_keeps_fact_but_disables_retry(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        payload = {
            "message": "model request failed (402): Insufficient Balance",
            "kind": "protocol",
            "retryable": True,
            "retries_used": 2,
            "status_code": 402,
        }
        with sqlite3.connect(repository.path) as connection:
            connection.execute(
                "INSERT INTO model_failures VALUES (?, ?, ?, ?)",
                (thread, turn, 0, json.dumps(payload)),
            )
        failure = await repository.load_model_failure(thread, turn, 0)
        assert failure.retryable is True
        assert failure.error().retryable is False
        assert str(failure.error()) == payload["message"]
        await repository.save_model_failure(thread, turn, 0, failure)
        with sqlite3.connect(repository.path) as connection:
            row = connection.execute("SELECT payload_json FROM model_failures").fetchone()
            assert json.loads(row[0]) == payload

    asyncio.run(scenario())


@pytest.mark.parametrize("order", ["success_first", "failure_first", "concurrent"])
def test_attempt_success_and_failure_are_mutually_exclusive(tmp_path, order):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        failure = ModelFailure("lost", "transport", True, 0)

        async def success():
            await repository.commit_model_step(thread, turn, 0, ModelCompleted(()))

        async def failed():
            await repository.save_model_failure(thread, turn, 0, failure)

        if order == "concurrent":
            results = await asyncio.gather(success(), failed(), return_exceptions=True)
            assert sum(isinstance(result, StorageIntegrityError) for result in results) == 1
        else:
            first, second = (success, failed) if order == "success_first" else (failed, success)
            await first()
            with pytest.raises(StorageIntegrityError):
                await second()
        completed = await repository.load_model_step(thread, turn, 0)
        recorded_failure = await repository.load_model_failure(thread, turn, 0)
        assert (completed is None) != (recorded_failure is None)

    asyncio.run(scenario())


@pytest.mark.parametrize("write_error", [False, True])
def test_cancelled_failure_write_is_joined_without_masking_cancellation(tmp_path, write_error):
    async def scenario():
        started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        class Repository(SQLiteSessionRepository):
            def _save_model_failure(self, *args):
                loop.call_soon_threadsafe(started.set)
                try:
                    assert release.wait(2)
                    if write_error:
                        raise RuntimeError("fixture write error")
                    super()._save_model_failure(*args)
                finally:
                    finished.set()

        repository = Repository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        failure = ModelFailure("disconnected", "transport", True, 0)
        task = asyncio.create_task(repository.save_model_failure(thread, turn, 0, failure))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert (await repository.load_model_failure(thread, turn, 0)) == (
            None if write_error else failure
        )

    asyncio.run(scenario())
