import asyncio
import os
import shlex
import sys

import pytest

from corki.core.stop_hooks import StopCommand, outcome, run_command
from corki.platforms.liveness import process_alive
from corki.shell import ShellType, default_user_shell


@pytest.mark.parametrize("failure", ["timeout", "overflow", "cancel"])
def test_stop_command_cleanup_joins_child(tmp_path, failure):
    async def scenario():
        script = (
            "import os,time; from pathlib import Path; "
            "Path('pid').write_text(str(os.getpid())); "
            + ("print('x'*1100000, flush=True); " if failure == "overflow" else "")
            + "time.sleep(120)"
        )
        script_path = tmp_path / "hook script.py"
        script_path.write_text(script, encoding="utf-8")
        shell = default_user_shell()
        if shell.kind == ShellType.POWERSHELL:
            quoted = [
                "'" + part.replace("'", "''") + "'" for part in (sys.executable, str(script_path))
            ]
            invocation = "& " + " ".join(quoted)
        else:
            invocation = shlex.join([sys.executable, str(script_path)])
        timeout = 30 if os.name == "nt" else 1
        command = StopCommand("fixture", "hash", invocation, timeout)
        task = asyncio.create_task(
            run_command(
                command, {}, shell=shell, cwd=tmp_path, environment=dict(os.environ)
            )
        )
        try:
            if failure == "cancel":
                async with asyncio.timeout(60):
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
                await asyncio.wait_for(task, timeout + 30)
            pid = int((tmp_path / "pid").read_text())
            assert not process_alive(pid)
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
