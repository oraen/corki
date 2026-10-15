"""Closing a live Turn must admit its Interrupt before stopping async hooks."""

import asyncio
import json
import os
import shlex
import sys

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.protocol.events import TurnCancelled
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("realtime", [False, True])
def test_close_admits_then_reaps_async_interrupt(tmp_path, monkeypatch, realtime):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import os,time; from pathlib import Path; "
                "Path('pid').write_text(str(os.getpid())); time.sleep(30)",
            ]
        )
        fingerprint, _ = command_identity(
            {"type": "command", "command": command, "async": True, "timeout": 3},
            event_name="Interrupt",
        )
        path = tmp_path / "config.toml"
        document = (
            "[[hooks.Interrupt]]\n[[hooks.Interrupt.hooks]]\n"
            f'type="command"\ncommand={json.dumps(command)}\nasync=true\ntimeout=3\n'
            f"[hooks.state.{json.dumps(f'{path}:interrupt:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        started, finished = asyncio.Event(), asyncio.Event()

        class Model:
            async def stream(self, request):
                started.set()
                try:
                    await asyncio.Event().wait()
                    yield
                finally:
                    finished.set()

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        owner = runtime._graph._stop_hooks._async
        close_owner = owner.aclose
        admitted, pids, events = [], [], []

        async def observe_close():
            admitted.append(bool(owner._tasks))
            if owner._tasks:
                # Expose the already-scheduled worker's real process before cleanup.
                # Do not invent a task or bypass normal admission.
                async with asyncio.timeout(2):
                    while not (tmp_path / "pid").exists():
                        await asyncio.sleep(0.01)
                pids.append(int((tmp_path / "pid").read_text()))
                os.kill(pids[-1], 0)
            await close_owner()

        monkeypatch.setattr(owner, "aclose", observe_close)

        async def consume():
            try:
                async for event in runtime.stream("CLOSE_ME", realtime=realtime):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(8):
                await started.wait()
                await runtime.aclose()
                await consumer
            assert finished.is_set()
            assert admitted == [True]
            assert len(pids) == 1
            with pytest.raises(ProcessLookupError):
                os.kill(pids[0], 0)
            assert len([event for event in events if isinstance(event, TurnCancelled)]) == 1
            assert not owner._tasks
            await runtime.aclose()
            assert admitted == [True]
        finally:
            # Preserve a failing close result while still joining its owned work.
            await asyncio.gather(runtime.aclose(), consumer, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("realtime", [False, True])
@pytest.mark.parametrize("intent_committed", [False, True])
def test_close_preserves_cancel_terminal_after_intent_write_error(
    tmp_path, monkeypatch, realtime, intent_committed
):
    async def scenario():
        started = asyncio.Event()
        samples, closed, events = [], [], []

        class Model:
            async def stream(self, request):
                samples.append(request)
                started.set()
                await asyncio.Event().wait()
                yield

            async def aclose(self):
                closed.append(self)

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
                home_path=tmp_path,
                thread_id=thread,
            )

        warm = await create()
        save_batch = warm._repository.save_hook_batch
        fault = OSError("cancel intent storage unavailable")

        async def fail_intent(*args):
            if not args[2].startswith("turn_cancellation:"):
                return await save_batch(*args)
            if intent_committed:
                await save_batch(*args)
            raise fault

        monkeypatch.setattr(warm._repository, "save_hook_batch", fail_intent)

        async def consume():
            try:
                async for event in warm.stream("CANCEL_WITH_STORAGE_ERROR", realtime=realtime):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(5):
                await started.wait()
                results = await asyncio.gather(warm.aclose(), warm.aclose(), return_exceptions=True)
                await consumer
            assert results == [fault, fault]
            assert len(closed) == 1
            terminals = [event for event in events if isinstance(event, TurnCancelled)]
            assert len(terminals) == 1
            with pytest.raises(OSError) as repeated:
                await warm.aclose()
            assert repeated.value is fault
            assert len(closed) == 1
        finally:
            await asyncio.gather(warm.aclose(), consumer, return_exceptions=True)

        cold = await create(warm.thread_id)
        try:
            turn = terminals[0].turn_id
            assert (
                await cold._repository.load_turn_status(warm.thread_id, turn)
                == TurnStatus.CANCELLED
            )
            intent = await cold._repository.load_hook_batch(
                warm.thread_id, turn, f"turn_cancellation:{turn}"
            )
            if intent_committed:
                assert intent is not None
                assert intent[0] == {"version": 1, "reason": "interrupted"} and not intent[1]
            else:
                assert intent is None
            assert [event async for event in cold.resume_pending()] == []
            assert len(samples) == 1
        finally:
            await cold.aclose()
        assert len(closed) == 2

    asyncio.run(scenario())
