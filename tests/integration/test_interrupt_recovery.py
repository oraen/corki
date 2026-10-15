"""A failed terminal write must not turn cancellation into a new model request."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("marker_enabled", [False, True])
@pytest.mark.parametrize("result_committed", [False, True])
@pytest.mark.parametrize(
    "hook_enabled,legacy,intent_fault",
    [
        (False, False, None),
        (True, False, None),
        (True, True, None),
        (False, False, "after_commit"),
        (True, False, "before_commit"),
        (True, False, "after_commit"),
    ],
)
def test_interrupt_cold_recovery_does_not_resample_or_repeat(
    tmp_path,
    monkeypatch,
    caplog,
    marker_enabled,
    result_committed,
    hook_enabled,
    legacy,
    intent_fault,
):
    async def scenario():
        path = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"}, event_name="Interrupt"
        )
        document = (
            '[[hooks.Interrupt]]\n[[hooks.Interrupt.hooks]]\ntype="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{path}:interrupt:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            agent_interrupt_message_enabled=marker_enabled,
            configuration=LocalConfigState(
                (ConfigLayer(path, "user", contents=document),) if hook_enabled else ()
            ),
        )
        started = asyncio.Event()
        samples, effects = [], []

        class Model:
            def __init__(self, cold):
                self.cold = cold

            async def stream(self, request):
                samples.append(self.cold)
                if not self.cold:
                    started.set()
                    await asyncio.Event().wait()
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def runner(command, payload, **kwargs):
            effects.append(payload)
            return {"exit_code": 0, "stdout": "{}", "stderr": ""}

        monkeypatch.setattr("corki.core.interrupt_hooks.run_command", runner)

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings
                if thread is None
                else replace(settings, configuration=LocalConfigState(())),
                model=Model(thread is not None),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        warm = await create()
        events = []

        async def consume(stream):
            try:
                async for event in stream:
                    events.append(event)
            except asyncio.CancelledError:
                pass

        save, complete = warm._repository.save_turn, warm._repository.complete_hook_execution
        save_batch = warm._repository.save_hook_batch

        async def legacy_batch(*args):
            if args[2].startswith("turn_cancellation:"):
                # Represent the old format without deleting existing data.
                return
            await save_batch(*args)

        async def fail_intent(*args):
            if not args[2].startswith("turn_cancellation:"):
                return await save_batch(*args)
            if intent_fault == "after_commit":
                await save_batch(*args)
            raise OSError(f"cancel intent {intent_fault}")

        async def fail_terminal(record):
            if record.status == TurnStatus.CANCELLED:
                raise OSError("terminal unavailable")
            await save(record)

        async def fail_result(*args):
            if result_committed:
                await complete(*args)
            raise OSError("interrupt result boundary")

        try:
            with monkeypatch.context() as patch:
                if legacy:
                    patch.setattr(warm._repository, "save_hook_batch", legacy_batch)
                elif intent_fault:
                    patch.setattr(warm._repository, "save_hook_batch", fail_intent)
                patch.setattr(warm._repository, "save_turn", fail_terminal)
                patch.setattr(warm._repository, "retry_turn_terminal", fail_terminal)
                patch.setattr(warm._repository, "complete_hook_execution", fail_result)
                task = asyncio.create_task(consume(warm.stream("ORIGINAL")))
                async with asyncio.timeout(5):
                    await started.wait()
                    await warm.cancel_active()
                    await task
            assert isinstance(events[-1], TurnCancelled)
            thread, turn = warm.thread_id, events[-1].turn_id
            assert await warm._repository.load_turn_status(thread, turn) == TurnStatus.RUNNING
            facts = await warm._repository.load_hook_executions(thread, turn, "interrupt_hook:")
            assert len(facts) == len(effects) == int(hook_enabled)
            if intent_fault:
                assert "Could not persist Turn cancellation intent" in caplog.text
                assert f"cancel intent {intent_fault}" in caplog.text
            if legacy or intent_fault == "before_commit":
                assert (
                    await warm._repository.load_hook_batch(
                        thread, turn, f"turn_cancellation:{turn}"
                    )
                    is None
                )
            else:
                intent, records = await warm._repository.load_hook_batch(
                    thread, turn, f"turn_cancellation:{turn}"
                )
                assert intent == {"version": 1, "reason": "interrupted"} and not records
        finally:
            # Crash simulation loses process-owned writes; normal close now drains them.
            warm._pending_terminals.clear()
            await warm.aclose()
        cold = await create(thread)
        try:
            events.clear()
            await consume(cold.resume_pending())
            assert isinstance(events[-1], TurnCancelled)
            assert samples == [False], "cancelled work was sampled again"
            assert len(effects) == int(hook_enabled)
            assert (
                await cold._repository.load_hook_executions(thread, turn, "interrupt_hook:")
                == facts
            )
            assert await cold._repository.load_turn_status(thread, turn) == TurnStatus.CANCELLED
            intent, records = await cold._repository.load_hook_batch(
                thread, turn, f"turn_cancellation:{turn}"
            )
            assert intent == {"version": 1, "reason": "interrupted"} and not records
            assert [event async for event in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
