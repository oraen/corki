"""The real CLI owns the query, receives replies, and preserves typed input."""

import os
import sys
from io import StringIO

import pexpect
import pytest

PROGRAM = """
import asyncio
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings

async def main():
    ui = TerminalUI(CorkiSettings(Path.cwd()), Path('input-history'))
    assert await ui.read_message() == 'draft followup'
    palette = ui._terminal_palette
    print('PALETTE=' + repr((palette.foreground, palette.background, palette.light)), flush=True)
    assert not ui._session.app.input.vt100_parser.color_listeners
    assert await ui.read_message() == 'second'
    assert list(ui._session.history.get_strings()) == ['draft followup', 'second']
    print('INPUT_AND_HISTORY_PRESERVED', flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("columns", [40, 100])
def test_sampled_light_palette_reaches_actual_patch_details(tmp_path, columns):
    program = """
import asyncio
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings
from corki.mcp.elicitation import ElicitationRequest

async def main():
    ui = TerminalUI(CorkiSettings(Path.cwd()), Path('input-history'))
    request = ElicitationRequest('patch', 'fixture', {
        'message': 'Inspect this patch.',
        '_meta': {'tool_params': {'patch': 'raw evidence', 'changes': [
            {'path': 'a.py', 'change': {'kind': 'add', 'content': 'print("visible")'}}
        ]}},
        'requestedSchema': {'type': 'object', 'properties': {}},
    }, 'patch_approval')
    result = await ui.read_elicitation(request)
    assert result[0] == 'decline'
    assert ui._terminal_palette.light
    assert list(ui._form_session.history.get_strings()) == []
    print('LIGHT_DETAILS_DECLINED', flush=True)

asyncio.run(main())
"""
    child = pexpect.spawn(
        sys.executable,
        ["-c", program],
        cwd=tmp_path,
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "PROMPT_TOOLKIT_COLOR_DEPTH": "DEPTH_24_BIT",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
        encoding="utf-8",
        timeout=8,
        dimensions=(30, columns),
    )
    output = StringIO()
    child.logfile_read = output
    try:
        child.expect_exact("\x1b]10;?\x1b\\\x1b]11;?\x1b\\")
        child.delaybeforesend = 0
        # Open details in the same input batch as the response. The sampler
        # finishes asynchronously and must invalidate the already-open pager.
        child.send("\x1b]10;rgb:00/00/00\x07\x1b]11;rgb:ff/ff/ff\x07\x01")
        try:
            child.expect("48;2;172;238;187")
        except pexpect.TIMEOUT:
            pytest.fail(f"light details missing gutter style: {output.getvalue()[-6000:]!r}")
        child.expect("48;2;218;251;225")
        child.expect("visible")
        child.send("qd")
        child.expect("LIGHT_DETAILS_DECLINED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)


@pytest.mark.parametrize("response", ["light", "dark", "none", "partial"])
def test_real_startup_probe_is_bounded_and_does_not_repeat(tmp_path, response):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
        encoding="utf-8",
        timeout=8,
        dimensions=(30, 80),
    )
    query = "\x1b]10;?\x1b\\\x1b]11;?\x1b\\"
    try:
        child.expect_exact(query)
        child.delaybeforesend = 0
        reply = "draft"
        if response != "none":
            reply += "\x1b]10;rgb:00/00/00\x07"
        if response in {"light", "dark"}:
            rgb = "ff/ff/ff" if response == "light" else "00/00/00"
            reply += "\x1b]11;rgb:" + rgb + "\x1b\\"
        child.send(reply)
        # No input is submitted while the optional probe times out/completes.
        assert child.expect(["PALETTE=", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send(" followup\r")
        expected = {
            "light": "((0, 0, 0), (255, 255, 255), True)",
            "dark": "((0, 0, 0), (0, 0, 0), False)",
        }.get(response, "(None, None, False)")
        child.expect_exact("PALETTE=" + expected)
        child.expect("Ask Corki to do anything")
        assert child.expect_exact([query, pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("second\r")
        child.expect("INPUT_AND_HISTORY_PRESERVED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
