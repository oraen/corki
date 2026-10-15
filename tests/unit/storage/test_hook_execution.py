"""Hook effects are durable but never masquerade as model tool observations."""

import asyncio

import pytest

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError
from corki.storage.volatile import VolatileSessionRepository


@pytest.mark.parametrize("volatile", [False, True])
def test_hook_claim_is_once_only_and_completed_result_is_immutable(tmp_path, volatile):
    async def scenario():
        path = tmp_path / "hooks.db"
        repository = VolatileSessionRepository() if volatile else SQLiteSessionRepository(path)
        thread, turn = new_thread_id(), new_turn_id()
        request = {"command": "fixture", "step": 1, "hash": "approved"}
        result = {"decision": "block", "reason": "verify"}
        try:
            await repository.create_thread(thread, tmp_path)
            assert not await repository.has_hook_executions(thread, turn, "exec")
            assert await repository.claim_hook_execution(thread, turn, "execution", request) is None
            assert await repository.has_hook_executions(thread, turn, "exec")
            assert not await repository.has_hook_executions(new_thread_id(), turn, "exec")
            assert not await repository.has_hook_executions(thread, new_turn_id(), "exec")
            assert not await repository.has_hook_executions(thread, turn, "exec%")
            assert not await repository.has_hook_executions(thread, turn, "other-step:")
            with pytest.raises(RuntimeError, match="unknown"):
                await repository.claim_hook_execution(thread, turn, "execution", request)
            await repository.complete_hook_execution(thread, turn, "execution", request, result)
            await repository.complete_hook_execution(thread, turn, "execution", request, result)
            if not volatile:
                await repository.close()
                repository = SQLiteSessionRepository(path)
            assert await repository.has_hook_executions(thread, turn, "exec")
            assert (
                await repository.claim_hook_execution(thread, turn, "execution", request) == result
            )
            assert await repository.load_turn_tool_outcomes(thread, turn) == ()
            assert await repository.load_items(thread) == ()
            with pytest.raises(StorageIntegrityError, match="overwritten"):
                await repository.complete_hook_execution(
                    thread, turn, "execution", request, {"decision": "allow"}
                )
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_concurrent_hook_claim_has_one_owner_and_survives_reopen(tmp_path):
    async def scenario():
        path = tmp_path / "hooks.db"
        repository = SQLiteSessionRepository(path)
        thread, turn = new_thread_id(), new_turn_id()
        request = {"command": "fixture", "step": 1}
        try:
            await repository.create_thread(thread, tmp_path)
            claims = await asyncio.gather(
                *(
                    repository.claim_hook_execution(thread, turn, "execution", request)
                    for _ in range(4)
                ),
                return_exceptions=True,
            )
            assert sum(result is None for result in claims) == 1
            assert sum(isinstance(result, RuntimeError) for result in claims) == 3
            await repository.close()
            repository = SQLiteSessionRepository(path)
            with pytest.raises(RuntimeError, match="unknown"):
                await repository.claim_hook_execution(thread, turn, "execution", request)
            assert await repository.load_turn_tool_outcomes(thread, turn) == ()
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["thread", "turn", "request"])
def test_hook_identity_cannot_be_reassigned(tmp_path, field):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "hooks.db")
        thread, turn = new_thread_id(), new_turn_id()
        request = {"command": "one"}
        try:
            await repository.create_thread(thread, tmp_path)
            await repository.claim_hook_execution(thread, turn, "execution", request)
            changed = (
                new_thread_id() if field == "thread" else thread,
                new_turn_id() if field == "turn" else turn,
                "execution",
                {"command": "two"} if field == "request" else request,
            )
            with pytest.raises(StorageIntegrityError, match="collision"):
                await repository.claim_hook_execution(*changed)
            with pytest.raises(StorageIntegrityError, match="elsewhere"):
                await repository.complete_hook_execution(*changed, {"decision": "allow"})
            with pytest.raises(RuntimeError, match="unknown"):
                await repository.claim_hook_execution(thread, turn, "execution", request)
        finally:
            await repository.close()

    asyncio.run(scenario())
