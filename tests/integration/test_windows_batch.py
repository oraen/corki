"""Actual Windows batch launch; does not substitute a mocked shell for cmd.exe."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from corki.execution import bundled
from corki.platforms.processes import close_job, create_subprocess_exec

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows batch execution")


@pytest.fixture
def native_batch(monkeypatch):
    configured = os.environ.get("CORKI_TEST_NATIVE_BATCH")
    if not configured or not Path(configured).is_file():
        pytest.fail("Build the native helper and set CORKI_TEST_NATIVE_BATCH")
    monkeypatch.setattr(bundled, "bundled_compiler", lambda: Path(configured).resolve())


@pytest.mark.parametrize("program", ["fixture", "fixture.v1"])
def test_batch_tool_preserves_arguments_protocol_and_separate_streams(tmp_path, native_batch, program):
    directory = tmp_path / "tools with spaces"
    directory.mkdir()
    receiver = directory / "receive.py"
    receiver.write_text(
        "import json,sys\n"
        "value=json.load(sys.stdin)\n"
        "print(json.dumps({'args':sys.argv[1:],'input':value},ensure_ascii=False))\n"
        "print('DIAGNOSTIC',file=sys.stderr)\n",
        encoding="utf-8",
    )
    script = directory / f"{program}.cmd"
    script.write_text(
        f'@echo off\n"{sys.executable}" -X utf8 "{receiver}" %*\n', encoding="utf-8"
    )
    arguments = ["hello world", "中文", "%COMSPEC%", "amp&value", 'quoted"value']
    environment = dict(os.environ, PATH=str(directory), PATHEXT=".EXE;.CMD")

    async def scenario():
        process = await create_subprocess_exec(
            program, *arguments, env=environment, cwd=tmp_path,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(90):
                output, errors = await process.communicate('{"message":"你好"}'.encode())
                assert process.returncode == 0, errors.decode(errors="replace")
                assert json.loads(output) == {"args": arguments, "input": {"message": "你好"}}
                assert errors.strip() == b"DIAGNOSTIC"
        finally:
            close_job(process)
            process._transport.close()
            await process.wait()

    asyncio.run(scenario())


def test_mcp_batch_server_initializes_calls_and_closes(tmp_path, native_batch):
    from corki.config import MCPServerSettings
    from corki.mcp.client import StdioMCPClient

    server = Path(__file__).parents[1] / "fixtures/mcp_number_server.py"
    directory = tmp_path / "MCP tools"
    directory.mkdir()
    (directory / "mcp-fixture.cmd").write_text(
        f'@echo off\n"{sys.executable}" -X utf8 -u "{server}"\n', encoding="utf-8"
    )
    settings = MCPServerSettings(
        "batch-fixture", "stdio", command="mcp-fixture", cwd=tmp_path,
        env=(("PATH", str(directory)), ("PATHEXT", ".EXE;.CMD")), timeout_seconds=60,
    )

    async def scenario():
        client = StdioMCPClient(settings)
        process = None
        try:
            await client.start()
            process = client._process
            tools = await client.list_tools()
            assert [tool["name"] for tool in tools] == ["read"]
            arguments = {"message": "中文 %literal% & value"}
            result = await client.call_tool("read", arguments)
            echoed = json.loads(result["content"][0]["text"])
            assert echoed["params"]["arguments"] == arguments
        finally:
            await client.aclose()
        assert client.is_closed
        assert process.returncode is not None and not process._corki_job_finalizer.alive

    asyncio.run(scenario())
