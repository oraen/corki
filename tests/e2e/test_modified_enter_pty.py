"""Exercise enhanced keys and mode restoration through an actual PTY."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio
import json
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings

async def main():
    cwd = Path.cwd()
    ui = TerminalUI(CorkiSettings(cwd), cwd / "history")
    result = await ui.read_message()
    print("MESSAGE=" + json.dumps(result), flush=True)
    try:
        await ui.read_message()
    except (EOFError, KeyboardInterrupt):
        print("INPUT_CLOSED", flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("columns", [40, 100])
@pytest.mark.parametrize("newline", ["\x1b[13;2u", "\x1b[27;2;13~", "\x1b\r"])
@pytest.mark.parametrize("exit_key", ["c", "d"])
def test_modified_enter_retains_draft_and_restores_keyboard(tmp_path, columns, newline, exit_key):
    environment = dict(os.environ, TERM="xterm-256color", PROMPT_TOOLKIT_NO_CPR="1")
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        timeout=10,
        dimensions=(24, columns),
    )
    try:
        child.expect_exact("\x1b[>4;2m\x1b[>1u")
        child.expect_exact("Ask Corki to do anything")
        child.send("first" + newline + "second")
        child.expect_exact("second")
        assert child.expect(["MESSAGE=", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("\r")
        child.expect_exact("\x1b[<u\x1b[>4;0m")
        child.expect_exact('MESSAGE="first\\nsecond"')
        child.expect_exact("\x1b[>4;2m\x1b[>1u")
        child.expect_exact("Ask Corki to do anything")
        child.sendcontrol(exit_key)
        child.expect_exact("\x1b[<u\x1b[>4;0m")
        child.expect_exact("INPUT_CLOSED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
