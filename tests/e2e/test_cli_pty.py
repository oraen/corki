import os
import re
import sys
import time
from pathlib import Path

import pexpect
import pytest


@pytest.mark.parametrize("columns", [40, 100])
@pytest.mark.parametrize("exit_key", ["c", "d"])
def test_corki_command_local_status_restores_composer_and_exits(
    tmp_path: Path,
    columns: int,
    exit_key: str,
) -> None:
    environment = dict(os.environ)
    environment["CORKI_HOME"] = str(tmp_path / ".corki")
    environment["TERM"] = "xterm-256color"
    environment["PROMPT_TOOLKIT_NO_CPR"] = "1"
    environment.pop("CORKI_API_KEY", None)
    environment.pop("OPENAI_API_KEY", None)
    child = pexpect.spawn(
        sys.executable,
        ["-m", "corki"],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        timeout=10,
        dimensions=(30, columns),
    )
    try:
        child.expect("Corki")
        child.expect("Ask Corki to do anything")
        child.send("/sta")
        child.expect("/status")
        child.send("\x1b")
        # Let the terminal parser distinguish bare Escape from an Alt-key chord.
        assert child.expect(["Model:", pexpect.TIMEOUT], timeout=0.7) == 1
        child.send("\r")
        child.expect("Unknown command: /sta")
        child.expect("Ask Corki to do anything")
        child.send("/sta")
        child.expect("/status")
        child.send("\t")
        assert child.expect(["Model:", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("\r")
        child.expect("Model:")
        child.expect("Directory:")
        child.expect("Corki home:")
        child.expect("Ask Corki to do anything")
        # The first candidate is highlighted by default; Down selects /status
        # without accepting it. Enter dispatches the highlighted command.
        child.send("/")
        child.expect("/status")
        child.send("\x1b[B")
        assert child.expect(["Model:", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("\r")
        child.expect("Model:")
        child.expect("Directory:")
        child.expect("Corki home:")
        child.expect("Ask Corki to do anything")
        child.send("/mcp")
        time.sleep(0.15)
        child.send("\r")
        child.expect_exact("No MCP tools currently available.")
        child.expect("Ask Corki to do anything")
        child.sendcontrol(exit_key)
        child.expect("Session ended")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)


@pytest.mark.parametrize("columns", [40, 100])
def test_ctrl_c_clears_draft_and_keeps_cli_usable(tmp_path: Path, columns: int) -> None:
    environment = dict(os.environ)
    environment.update(
        CORKI_HOME=str(tmp_path / ".corki"),
        TERM="xterm-256color",
        PROMPT_TOOLKIT_NO_CPR="1",
    )
    environment.pop("CORKI_API_KEY", None)
    environment.pop("OPENAI_API_KEY", None)
    child = pexpect.spawn(
        sys.executable,
        ["-m", "corki"],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        timeout=10,
        dimensions=(30, columns),
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("/status")
        child.expect_exact("/status")
        child.sendcontrol("c")
        child.expect("Ask Corki to do anything")
        assert "Session ended" not in child.before
        # Recover the cancelled draft and submit: no model or external service is needed.
        child.send("\x1b[A\r")
        child.expect("Model:")
        child.expect("Directory:")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect("Session ended")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)


@pytest.mark.parametrize("columns", [40, 100])
def test_ctrl_d_with_nonempty_draft_does_not_exit_or_lose_input(
    tmp_path: Path, columns: int
) -> None:
    environment = dict(os.environ)
    environment.update(
        CORKI_HOME=str(tmp_path / ".corki"),
        TERM="xterm-256color",
        PROMPT_TOOLKIT_NO_CPR="1",
    )
    environment.pop("CORKI_API_KEY", None)
    environment.pop("OPENAI_API_KEY", None)
    child = pexpect.spawn(
        sys.executable,
        ["-m", "corki"],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        timeout=10,
        dimensions=(30, columns),
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("/status")
        child.expect_exact("/status")
        child.sendcontrol("d")
        assert child.expect(["Session ended", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("\r")
        child.expect("Model:")
        child.expect("Directory:")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect("Session ended")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


HISTORY_FAILURE_PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(cwd)
    class Runtime:
        closed = False
        async def load_display_turns_page(self, **kwargs):
            raise OSError("PRIVATE_STORAGE_DETAIL")
        async def load_display_items_page(self, **kwargs):
            raise AssertionError("items should not load after turns fail")
        async def contains_display_item(self, **kwargs):
            return False
        async def load_display_snapshot(self):
            raise AssertionError("paged history must be used")
        async def resume_pending(self):
            raise AssertionError("must not resume after history fails")
            yield
        async def aclose(self):
            self.closed = True
    runtime = Runtime()
    ui = TerminalUI(settings, cwd / "input-history")
    result = await CorkiApplication(settings, CorkiPaths.from_home(cwd), runtime, ui).run()
    assert result == 1 and runtime.closed
    print("HISTORY_FAILURE_CLEAN_EXIT", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("columns", [40, 100])
def test_history_load_failure_has_clear_terminal_exit(tmp_path: Path, columns: int) -> None:
    child = pexpect.spawn(
        sys.executable,
        ["-c", HISTORY_FAILURE_PROGRAM],
        cwd=tmp_path,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
        encoding="utf-8",
        timeout=10,
        dimensions=(30, columns),
    )
    try:
        child.expect("Corki")
        child.expect("HISTORY_FAILURE_CLEAN_EXIT")
        rendered = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", child.before).split())
        assert "Could not load conversation history" in rendered
        assert "No new turn was started" in rendered
        assert "Session ended" in rendered
        assert "Ask Corki to do anything" not in rendered
        assert "PRIVATE_STORAGE_DETAIL" not in rendered
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
