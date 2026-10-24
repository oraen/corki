"""Real Windows command Hook output, including control responses and failures."""

import asyncio
import json
import os

import pytest

from corki.core.stop_hooks import StopCommand, outcome, run_command
from corki.shell import ShellType, get_shell

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")


@pytest.mark.parametrize("failure", [False, True])
def test_windows_hook_utf8_control_and_diagnostics(tmp_path, failure):
    shell = get_shell(ShellType.POWERSHELL)
    assert shell is not None
    directory = tmp_path / "中文 Hook"
    directory.mkdir()
    response = {"continue": False, "stopReason": "知识库测试"}
    script = "[Console]::WriteLine('" + json.dumps(response, ensure_ascii=False) + "')"
    if failure:
        script = "[Console]::Error.WriteLine('需要用户确认'); exit 2"

    async def scenario():
        result = await run_command(
            StopCommand("fixture", "hash", script, 60), {},
            shell=shell, cwd=directory, environment=dict(os.environ),
        )
        if failure:
            assert result["exit_code"] == 2
            assert result["stderr"].strip() == "需要用户确认"
            assert outcome(result)[:2] == ("block", "需要用户确认")
        else:
            assert result["exit_code"] == 0
            assert json.loads(result["stdout"]) == response
            assert outcome(result)[:2] == ("stop", "知识库测试")

    asyncio.run(scenario())
