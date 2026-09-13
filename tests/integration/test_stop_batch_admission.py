"""No Hook effect may cross a failed or cancelled batch admission."""

import asyncio
import json
import shlex
import sys
import threading

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookStarted, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize("ephemeral", [False, True])
@pytest.mark.parametrize("fault", ["insert_failure", "cancel_before_write"])
def test_batch_admission_precedes_every_hook_effect(tmp_path, monkeypatch, ephemeral, fault):
    async def scenario():
        marker = tmp_path / "effect"
        command = shlex.join(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
        )
        fingerprint = command_identity({"type": "command", "command": command})[0]
        source = tmp_path / "config.toml"
        document = (
            '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype="command"\ncommand='
            + json.dumps(command)
            + "\n[hooks.state."
            + json.dumps(f"{source}:stop:0:0")
            + "]\ntrusted_hash="
            + json.dumps(fingerprint)
            + "\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=tmp_path / "state.db",
            ephemeral=ephemeral,
        )
        entered, release = asyncio.Event(), threading.Event()
        events, tasks = [], []
        repository = runtime._repository
        original = repository._save_hook_batch
        loop = asyncio.get_running_loop()

        def held(*args):
            if not args[2].startswith("stop:"):
                return original(*args)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            original(*args)

        async def consume():
            async for event in runtime.stream("finish"):
                events.append(event)

        try:
            await runtime._ensure_ready()
            if fault == "insert_failure":
                with repository._connect() as connection:
                    connection.execute(
                        "CREATE TRIGGER reject_batch BEFORE INSERT ON hook_batches "
                        "WHEN NEW.batch_key LIKE 'stop:%' "
                        "BEGIN SELECT RAISE(ABORT, 'batch admission denied'); END"
                    )
            else:
                monkeypatch.setattr(repository, "_save_hook_batch", held)
            work = asyncio.create_task(consume())
            tasks.append(work)
            if fault == "cancel_before_write":
                await asyncio.wait_for(entered.wait(), 5)
                # This API requests cancellation; the stream owns completion.
                await runtime.cancel_active()
                done, _ = await asyncio.wait((work,), timeout=0.05)
                assert not done, "cancellation abandoned the batch writer"
                assert not marker.exists()
                assert not any(isinstance(event, HookStarted) for event in events)
                release.set()
            if fault == "cancel_before_write":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(work, 5)
            else:
                await asyncio.wait_for(work, 5)
            expected = TurnFailed if fault == "insert_failure" else TurnCancelled
            assert isinstance(events[-1], expected), events
            if fault == "insert_failure":
                assert "batch admission denied" in events[-1].error
            assert len(requests) == 1
            assert not any(isinstance(event, (HookStarted, TurnCompleted)) for event in events)
            assert not marker.exists()
            with repository._connect() as connection:
                assert connection.execute("SELECT count(*) FROM hook_executions").fetchone()[0] == 0
                assert connection.execute(
                    "SELECT count(*) FROM hook_batches WHERE batch_key LIKE 'stop:%'"
                ).fetchone()[0] == (0 if fault == "insert_failure" else 1)
            # Positive control: the same approved command really is executable.
            # A new turn after removing the injected fault must create the marker.
            monkeypatch.setattr(repository, "_save_hook_batch", original)
            if fault == "insert_failure":
                with repository._connect() as connection:
                    connection.execute("DROP TRIGGER reject_batch")
            next_events = [event async for event in runtime.stream("try a new turn")]
            assert isinstance(next_events[-1], TurnCompleted), next_events
            assert any(isinstance(event, HookStarted) for event in next_events)
            assert marker.exists()
            assert len(requests) == 2
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
