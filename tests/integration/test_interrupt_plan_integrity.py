"""Cold recovery must not accept malformed legacy Interrupt plans as control state."""

import asyncio
from dataclasses import asdict

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import StopCommand
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "damage", [None, "missing_cwd", "model_type", "command_fields", "duplicate_key"]
)
@pytest.mark.parametrize("intent_present", [False, True])
def test_legacy_interrupt_plan_is_validated_before_cold_recovery(
    tmp_path, damage, intent_present, caplog, monkeypatch
):
    async def scenario():
        samples = []
        executions = []

        async def unexpected_execution(*args, **kwargs):
            executions.append((args, kwargs))
            raise AssertionError("Recovery must not execute this saved command")

        monkeypatch.setattr("corki.core.interrupt_hooks.run_command", unexpected_execution)

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

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path / "home",
                thread_id=thread,
            )

        warm = await create()
        try:
            await warm._ensure_ready()
            thread, turn = warm.thread_id, new_turn_id()
            key = f"interrupt_hook:{turn}:"
            payload = {
                "session_id": str(await warm._repository.load_thread_session_id(thread)),
                "turn_id": str(turn),
                "hook_event_name": "Interrupt",
                "cwd": str(tmp_path),
                "model": settings.model,
                "transcript_path": None,
                "permission_mode": "default",
            }
            commands = [asdict(StopCommand("old_hook", "old_fingerprint", "inspect", 1))]
            if damage == "missing_cwd":
                del payload["cwd"]
            elif damage == "model_type":
                payload["model"] = []
            elif damage == "command_fields":
                commands = [{}]
            elif damage == "duplicate_key":
                commands *= 2
            await warm._repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "input"))
            await warm._repository.append_items(thread, (UserMessageItem("input", turn),))
            await warm._repository.save_hook_batch(
                thread, turn, key, {"version": 1, "payload": payload, "commands": commands}
            )
            before = await warm._repository.load_hook_batch(thread, turn, key)
            intent_key = f"turn_cancellation:{turn}"
            if intent_present:
                await warm._repository.save_hook_batch(
                    thread, turn, intent_key, {"version": 1, "reason": "interrupted"}
                )
            intent_before = await warm._repository.load_hook_batch(thread, turn, intent_key)
        finally:
            await warm.aclose()

        cold = await create(thread)
        try:
            events = []
            try:
                async for event in cold.resume_pending():
                    events.append(event)
            except asyncio.CancelledError:
                pass
            terminals = [e for e in events if isinstance(e, (TurnCancelled, TurnFailed))]
            assert len(terminals) == 1
            failed = damage is not None and not intent_present
            assert isinstance(terminals[0], TurnFailed if failed else TurnCancelled)
            assert samples == []
            assert executions == []
            assert await cold._repository.load_hook_batch(thread, turn, key) == before
            if intent_present:
                assert (
                    await cold._repository.load_hook_batch(thread, turn, intent_key)
                    == intent_before
                )
            if damage and intent_present:
                assert "Interrupt hook cleanup failed" in caplog.text
                assert "Invalid durable Interrupt" in caplog.text
            assert await cold._repository.load_turn_status(thread, turn) == (
                TurnStatus.FAILED if failed else TurnStatus.CANCELLED
            )
            assert [e async for e in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
