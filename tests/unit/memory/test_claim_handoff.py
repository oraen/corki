"""Cancellation retains both claim and cleanup owners, even under repeated cancel."""

import asyncio
import sqlite3
from contextlib import closing

import pytest

from corki.memory.claim_handoff import handoff_claim
from corki.memory.sqlite import SQLiteMemoryRepository


@pytest.mark.parametrize("failure", [None, "claim", "cleanup"])
def test_repeated_cancellation_joins_each_phase_and_preserves_control_flow(failure):
    async def scenario():
        claimed, release_claim, cleaning, release_cleanup = (asyncio.Event() for _ in range(4))
        warnings, accepted = [], []
        value = object()

        async def claim():
            claimed.set()
            await release_claim.wait()
            if failure == "claim":
                raise RuntimeError("claim fixture failure")
            return value

        async def cleanup(result):
            accepted.append(result)
            cleaning.set()
            await release_cleanup.wait()
            if failure == "cleanup":
                raise RuntimeError("cleanup fixture failure")

        task = asyncio.create_task(handoff_claim(claim(), on_cancel=cleanup, warn=warnings.append))
        await claimed.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release_claim.set()
        if failure != "claim":
            await asyncio.wait_for(cleaning.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and accepted == [value]
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert len(warnings) == (failure is not None)
        if failure:
            assert f"{failure} fixture failure" in warnings[0]
        assert accepted == ([] if failure == "claim" else [value])

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, True])
def test_memory_connection_context_closes_after_commit_or_rollback(tmp_path, failure):
    repository = object.__new__(SQLiteMemoryRepository)
    repository._path = tmp_path / "connection.db"
    connection = None
    try:
        try:
            with repository._connect() as connection:
                connection.execute("CREATE TABLE evidence(value INTEGER)")
                connection.execute("INSERT INTO evidence VALUES (1)")
                if failure:
                    raise RuntimeError("rollback fixture")
        except RuntimeError as error:
            assert failure and str(error) == "rollback fixture"
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
        with closing(sqlite3.connect(repository._path)) as check:
            assert check.execute("SELECT value FROM evidence").fetchall() == (
                [] if failure else [(1,)]
            )
    finally:
        if connection is not None:
            connection.close()
