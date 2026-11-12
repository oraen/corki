"""Inspect actual terminal cells through resize, interruption and the next draft."""

import os
import re
import sys
import time
from contextlib import suppress
from io import StringIO
from pathlib import Path

import pexpect
import pyte
import pytest

PROGRAM = r"""
import asyncio, json
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings

async def main():
    ui = TerminalUI(CorkiSettings(Path.cwd(), skills_enabled=False, plugins_enabled=False),
                    Path.cwd() / 'history')
    ui._terminal_palette.attempted = True
    ui._terminal_palette.background = (24,24,24)
    ui._show_submitted_input('A question with several words\nand an explicit continuation')
    ui.show_tool_started('exec_command', json.dumps({'cmd': "python -c 'print(1)' --fixture"}))
    ui.show_tool_output(''.join(f'output-{i:03d}\n' for i in range(30)))
    ui.show_assistant_message('Partial answer remains available.')
    ui.set_turn_active(True)
    ui.set_tool_activity("fixture", "read_files_with_a_long_status_description_and_details")
    try:
        await ui.read_message()
    except KeyboardInterrupt:
        ui.set_tool_activity("fixture", None)
        ui.set_turn_active(False)
        ui.show_notice('Answer stopped')
    assert await ui.read_message() == 'next draft'
    assert 'output-015' in ui._transcript.render(80, expand_tools=True)
    print('PRESENTATION_PTY_OK', flush=True)
asyncio.run(main())
"""


def screen_snapshot(output, width):
    screen = pyte.Screen(width, 30)
    pyte.Stream(screen).feed(re.sub(r"\x1b\[(?:>1u|<u|>4;[02]m)", "", output))
    return screen


@pytest.mark.parametrize("width", [40, 100])
def test_visual_layout_resize_and_stop_preserve_evidence_and_input(tmp_path, width):
    output = StringIO()
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        dimensions=(30, width),
        timeout=15,
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "CORKI_HOME": str(tmp_path / "home"),
        },
    )
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        time.sleep(0.15)
        with suppress(pexpect.TIMEOUT):
            child.read_nonblocking(65536, timeout=0.1)
        screen = screen_snapshot(output.getvalue(), width)
        lines = screen.display
        assert any("• python" in row for row in lines)
        assert any("  └ output-000" in row for row in lines)
        assert any("output-029" in row for row in lines)
        assert any("+26 lines" in row for row in lines)
        assert any("Partial answer" in row for row in lines)
        assert any("Working" in row for row in lines)
        directory = os.environ.get("CORKI_CLI_QA_OUTPUT")
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            (Path(directory) / f"presentation-{width}.txt").write_text("\n".join(lines))
        child.setwinsize(30, 80 if width == 40 else 40)
        time.sleep(0.15)
        child.send("\x1b")
        child.expect("Answer stopped")
        child.expect("Ask Corki to do anything")
        child.send("next draft")
        time.sleep(0.15)
        child.send("\r")
        child.expect("PRESENTATION_PTY_OK")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)


@pytest.mark.parametrize("height", [8, 12])
def test_short_terminal_keeps_composer_usable_during_long_status(tmp_path, height):
    output = StringIO()
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        dimensions=(height, 40),
        timeout=15,
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "CORKI_HOME": str(tmp_path / "home"),
        },
    )
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        time.sleep(0.2)
        with suppress(pexpect.TIMEOUT):
            child.read_nonblocking(65536, timeout=0.1)
        screen = pyte.Screen(40, height)
        pyte.Stream(screen).feed(re.sub(r"\x1b\[(?:>1u|<u|>4;[02]m)", "", output.getvalue()))
        assert any("Working" in row for row in screen.display)
        assert any("Ask Corki to do anything" in row for row in screen.display)
        child.send("\x1b")
        child.expect("Answer stopped")
        child.expect("Ask Corki to do anything")
        child.send("next draft")
        time.sleep(0.15)
        child.send("\r")
        child.expect("PRESENTATION_PTY_OK")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
