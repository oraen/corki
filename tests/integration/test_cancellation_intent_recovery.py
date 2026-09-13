"""Durable cancellation is control state, never a hint to resample a Turn."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "intent, claimed, valid",
    [
        ({"version": 1, "reason": reason}, False, True)
        for reason in (None, "interrupted", "replaced")
    ]
    + [
        ({"version": True, "reason": "interrupted"}, False, False),
        ({"version": 2, "reason": "interrupted"}, False, False),
        ({"version": 1}, False, False),
        ({"version": 1, "reason": "completed"}, False, False),
        ({"version": 1, "reason": []}, False, False),
        ({"version": 1, "reason": "interrupted", "extra": True}, False, False),
        ({"version": 1, "reason": "interrupted"}, True, False),
    ],
)
def test_cold_cancellation_intent_precedes_model_and_preserves_evidence(
    tmp_path, intent, claimed, valid
):
    async def scenario():
        samples = []

        class Model:
            async def stream(self, request):
                samples.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            agent_interrupt_message_enabled=False,
        )

        async def create(thread_id=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "session.db",
                home_path=tmp_path / "home",
                thread_id=thread_id,
            )

        warm = await create()
        try:
            await warm._ensure_ready()
            thread, turn = warm.thread_id, new_turn_id()
            key = f"turn_cancellation:{turn}"
            await warm._repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "input"))
            await warm._repository.append_items(thread, (UserMessageItem("input", turn),))
            await warm._repository.save_hook_batch(thread, turn, key, intent)
            if claimed:
                await warm._repository.claim_hook_execution(thread, turn, key + ":unexpected", {})
            before = await warm._repository.load_hook_batch(thread, turn, key)
        finally:
            await warm.aclose()

        cold = await create(thread)
        try:
            events = []

            async def resume():
                async for event in cold.resume_pending():
                    events.append(event)

            if valid:
                with pytest.raises(asyncio.CancelledError):
                    await resume()
            else:
                await resume()
            terminals = [e for e in events if isinstance(e, (TurnCancelled, TurnFailed))]
            assert len(terminals) == 1
            assert isinstance(terminals[0], TurnCancelled if valid else TurnFailed)
            if not valid:
                assert "Invalid durable Turn cancellation intent" in terminals[0].error
            assert samples == []
            assert await cold._repository.load_hook_batch(thread, turn, key) == before
            assert await cold._repository.load_turn_status(thread, turn) == (
                TurnStatus.CANCELLED if valid else TurnStatus.FAILED
            )
            assert [e async for e in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
