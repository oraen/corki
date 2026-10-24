"""Real Windows shell/UTF-8 header helper execution and cleanup."""

import asyncio
import sys

import pytest

from corki.mcp import header_helper_process as module

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell")


@pytest.mark.parametrize("failure", [False, True])
def test_windows_header_helper_utf8_and_exit_status(tmp_path, monkeypatch, failure):
    children = []
    original = module.create_subprocess_exec

    async def spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(module, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module, "HELPER_TIMEOUT", 60)

    async def scenario():
        command = "[Console]::WriteLine('{\"X-Test\":\"中文\"}')"
        if failure:
            command += "; exit 7"
            with pytest.raises(ValueError, match="status 7"):
                await module.run_helper(command, tmp_path)
        else:
            headers = await module.run_helper(command, tmp_path)
            assert headers["x-test"] == "中文"
        assert len(children) == 1 and children[0].returncode is not None
        assert not children[0]._corki_job_finalizer.alive

    asyncio.run(scenario())
