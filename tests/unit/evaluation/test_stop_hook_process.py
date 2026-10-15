import asyncio
import os
import shlex
import sys

import pytest

from corki.core.stop_hooks import StopCommand, outcome, run_command
from corki.shell import default_user_shell


@pytest.mark.parametrize("failure", ["timeout", "overflow", "cancel"])
def test_stop_command_cleanup_joins_child(tmp_path, failure):
    async def scenario():
        script = (
            "import os,time; from pathlib import Path; "
            "Path('pid').write_text(str(os.getpid())); "
            + ("print('x'*1100000, flush=True); " if failure == "overflow" else "")
            + "time.sleep(30)"
        )
        command = StopCommand("fixture", "hash", shlex.join([sys.executable, "-c", script]), 1)
        task = asyncio.create_task(
            run_command(
                command, {}, shell=default_user_shell(), cwd=tmp_path, environment=dict(os.environ)
            )
        )
        try:
            if failure == "cancel":
                async with asyncio.timeout(5):
                    while not (tmp_path / "pid").exists():
                        await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
            error = {
                "timeout": TimeoutError,
                "overflow": ValueError,
                "cancel": asyncio.CancelledError,
            }[failure]
            with pytest.raises(error):
                await asyncio.wait_for(task, 5)
            pid = int((tmp_path / "pid").read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("result", "decision"),
    [
        ({"exit_code": 2, "stdout": "", "stderr": " check "}, "block"),
        ({"exit_code": 2, "stdout": "", "stderr": ""}, "allow"),
        ({"exit_code": 0, "stdout": '{"decision":"block"}', "stderr": ""}, "allow"),
        ({"exit_code": 0, "stdout": '{"continue":false}', "stderr": ""}, "stop"),
        ({"exit_code": 0, "stdout": '{"continue":"false"}', "stderr": ""}, "allow"),
    ],
)
def test_stop_outcome_distinguishes_control_from_diagnostic(result, decision):
    actual, message, diagnostics = outcome(result)
    assert diagnostics == ()
    assert actual == decision
    if decision == "allow":
        assert "fail" in message or "invalid" in message
