"""Turn cancellation interrupts owned preflight helpers before model process admission."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.execution.owned_process import run_owned
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin import process


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("action", ["cancel", "close"])
def test_turn_cancellation_reaps_preflight_without_model_spawn(tmp_path, monkeypatch, mode, action):
    compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not compiler or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")
    if mode == "code_mode_only" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        entered, finalized = asyncio.Event(), asyncio.Event()
        helpers, launches, requests, events = [], [], [], []
        script = "import time; time.sleep(60)"
        spawn = asyncio.create_subprocess_exec

        async def capture(*args, **kwargs):
            child = await spawn(*args, **kwargs)
            if args == (sys.executable, "-c", script):
                helpers.append(child)
                entered.set()
            return child

        async def prepare(permissions, argv, cwd, **kwargs):
            try:
                await run_owned([sys.executable, "-c", script], b"", cwd=cwd, output_limit=100)
                return argv
            finally:
                finalized.set()

        async def forbidden_spawn(*args, **kwargs):
            launches.append(args)
            raise AssertionError("model command was admitted after cancellation")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
        monkeypatch.setattr(process, "sandbox_command", prepare)
        monkeypatch.setattr(process, "_spawn", forbidden_spawn)

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) == 1
                arguments = {"cmd": "printf PREFLIGHT_MUST_NOT_RUN", "login": False}
                call = (
                    ToolCall(new_tool_call_id(), "exec_command", arguments)
                    if mode == "direct"
                    else ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments="text(await tools.exec_command("
                        + json.dumps(arguments)
                        + "))",
                    )
                )
                yield ModelCompleted(
                    (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                execution_permissions=ExecutionPermissions(
                    Path(compiler), tmp_path, '{"type":"read-only"}'
                ),
            ),
            home_path=tmp_path / "home",
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )

        async def consume():
            try:
                async for event in runtime.stream("cancel during preparation"):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = asyncio.create_task(consume())
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closing = asyncio.create_task(
                runtime.cancel_active() if action == "cancel" else runtime.aclose()
            )
            _, pending = await asyncio.wait((consumer, closing), timeout=2)
            assert not pending, "Turn shutdown waited for the preflight helper timeout"
            await closing
            await consumer
            assert finalized.is_set()
            assert len(helpers) == 1 and helpers[0].returncode is not None
            assert not launches and len(requests) == 1
            assert not runtime._process_manager._starting
            assert not runtime._process_manager._sessions
            assert isinstance(events[-1], TurnCancelled), events[-1]
            assert (
                sum(
                    isinstance(event, (TurnCancelled, TurnCompleted, TurnFailed))
                    for event in events
                )
                == 1
            )
        finally:
            # Red-test cleanup owns only the exact temporary Python helper, not a Rust PID.
            for child in helpers:
                if child.returncode is None:
                    child.kill()
                await child.wait()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
