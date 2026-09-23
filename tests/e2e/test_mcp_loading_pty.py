"""Transient inventory activity leaves the composer usable and clears on exit."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.cli.mcp_inventory import MCPInventory
from corki.config import CorkiSettings

async def main():
    release = asyncio.Event()
    closed = asyncio.Event()
    class Runtime:
        async def mcp_tool_catalog(self):
            try:
                await release.wait()
                if sys.argv[1] == "failure":
                    raise ValueError("private credential")
                return ()
            finally:
                closed.set()
    ui = TerminalUI(CorkiSettings(Path.cwd()), Path("history"))
    inventory = MCPInventory(Runtime(), ui)
    try:
        assert await ui.read_message() == "/mcp"
        inventory.start()
        assert await ui.read_message() == "draft while loading"
        assert ui._mcp_loading
        if sys.argv[1] == "cancel":
            await inventory.aclose()
        else:
            release.set()
            await inventory.task
        assert not ui._mcp_loading and closed.is_set()
        assert not any("Loading MCP" in str(c) for c in ui._transcript.calls)
        assert await ui.read_message() == "followup"
    finally:
        await inventory.aclose()
    print("LOADING_CLEANED", flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_mcp_loading_keeps_input_available(tmp_path, width, outcome):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, outcome],
        cwd=tmp_path,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
        encoding="utf-8",
        timeout=10,
        dimensions=(30, width),
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("/mcp\r")
        child.expect_exact("Loading MCP tools")
        child.send("draft while loading\r")
        if outcome != "cancel":
            child.expect_exact("No MCP tools" if outcome == "success" else "discovery failed")
        child.expect("Ask Corki to do anything")
        child.send("followup\r")
        child.expect_exact("LOADING_CLEANED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
