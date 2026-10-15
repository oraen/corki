"""Recovery reads exact lifecycle ownership without interpreting missing as terminal."""

import asyncio
from dataclasses import replace

import pytest

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.sessions.models import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError
from corki.storage.volatile import VolatileSessionRepository


@pytest.mark.parametrize("volatile", [False, True])
def test_terminal_retry_is_scoped_idempotent_and_preserves_admission(tmp_path, volatile):
    async def scenario():
        repository = (
            VolatileSessionRepository()
            if volatile
            else SQLiteSessionRepository(tmp_path / "retry.db")
        )
        thread, turn = new_thread_id(), new_turn_id()
        pending = TurnRecord(turn, thread, TurnStatus.COMPLETED, "INPUT", final_answer="DONE")
        try:
            await repository.create_thread(thread, tmp_path)
            with pytest.raises(StorageIntegrityError):
                await repository.retry_turn_terminal(pending)
            admitted = replace(
                pending, status=TurnStatus.RUNNING, final_answer=None, base_instructions="FROZEN"
            )
            await repository.save_turn(admitted)
            for bad in (
                replace(pending, thread_id=new_thread_id()),
                replace(pending, user_input="OTHER"),
                replace(pending, operation="compact"),
            ):
                with pytest.raises(StorageIntegrityError):
                    await repository.retry_turn_terminal(bad)
            await repository.retry_turn_terminal(pending)
            await repository.retry_turn_terminal(pending)
            assert await repository.confirm_turn_terminal(pending)
            with pytest.raises(StorageIntegrityError):
                await repository.retry_turn_terminal(replace(pending, final_answer="CONFLICT"))
            assert await repository.confirm_turn_terminal(pending)
            with repository._connect() as connection:
                assert (
                    connection.execute(
                        "SELECT base_instructions FROM turns WHERE id=?", (str(turn),)
                    ).fetchone()[0]
                    == "FROZEN"
                )
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("volatile", [False, True])
def test_scoped_status_read_preserves_all_lifecycle_states(tmp_path, volatile):
    async def scenario():
        repository = (
            VolatileSessionRepository()
            if volatile
            else SQLiteSessionRepository(tmp_path / "status.db")
        )
        thread, other, turn = new_thread_id(), new_thread_id(), new_turn_id()
        try:
            await repository.create_thread(thread, tmp_path)
            await repository.create_thread(other, tmp_path)
            assert await repository.load_turn_status(thread, turn) is None
            for status in TurnStatus:
                await repository.save_turn(TurnRecord(turn, thread, status, "INPUT"))
                assert await repository.load_turn_status(thread, turn) is status
                assert await repository.load_turn_status(other, turn) is None
            with repository._connect() as connection:
                connection.execute("UPDATE turns SET status=? WHERE id=?", ("corrupt", str(turn)))
            with pytest.raises(StorageIntegrityError, match="invalid stored Turn status"):
                await repository.load_turn_status(thread, turn)
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("volatile", [False, True])
@pytest.mark.parametrize("status", [TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED])
def test_terminal_confirmation_requires_exact_scoped_payload(tmp_path, volatile, status):
    async def scenario():
        repository = (
            VolatileSessionRepository()
            if volatile
            else SQLiteSessionRepository(tmp_path / "confirm.db")
        )
        thread, turn = new_thread_id(), new_turn_id()
        record = TurnRecord(turn, thread, status, "INPUT", final_answer="ANSWER", error="ERROR")
        try:
            await repository.create_thread(thread, tmp_path)
            assert not await repository.confirm_turn_terminal(record)
            admitted = replace(record, status=TurnStatus.RUNNING, base_instructions="FROZEN")
            await repository.save_turn(admitted)
            assert not await repository.confirm_turn_terminal(record)
            with pytest.raises(ValueError, match="running"):
                await repository.confirm_turn_terminal(admitted)
            await repository.save_turn(record)
            assert await repository.confirm_turn_terminal(record)
            for field, value in (
                ("id", new_turn_id()),
                ("thread_id", new_thread_id()),
                (
                    "status",
                    TurnStatus.FAILED if status is not TurnStatus.FAILED else TurnStatus.COMPLETED,
                ),
                ("user_input", "OTHER"),
                ("operation", "compact"),
                ("final_answer", None),
                ("error", None),
            ):
                assert not await repository.confirm_turn_terminal(
                    replace(record, **{field: value})
                ), field
            assert await repository.confirm_turn_terminal(record)
            with repository._connect() as connection:
                assert (
                    connection.execute(
                        "SELECT base_instructions FROM turns WHERE id=?", (str(turn),)
                    ).fetchone()[0]
                    == "FROZEN"
                )
        finally:
            await repository.close()

    asyncio.run(scenario())
