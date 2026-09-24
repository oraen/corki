"""Composer surface covers explicit and visual lines without tinting its gap/footer."""

import os
import re
import sys
import time
from contextlib import suppress
from io import StringIO

import pexpect
import pyte
import pytest

PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings

async def main():
    ui = TerminalUI(CorkiSettings(Path.cwd()), Path.cwd() / "history")
    ui._terminal_palette.attempted = True
    ui._terminal_palette.background = BACKGROUND
    ui.show_work_duration(90)
    ui._draft = "first\n" + "多行输入" * 18 + "\nlast"
    result = await ui.read_message()
    assert result == "first\n" + "多行输入" * 18 + "\nlast"
    print("CLEAN_EXIT", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize(
    "background,expected", [((0, 0, 0), "1f1f1f"), ((255, 255, 255), "f5f5f5")]
)
def test_multiline_surface_and_gap(tmp_path, width, background, expected):
    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "PROMPT_TOOLKIT_NO_CPR": "1",
        "PROMPT_TOOLKIT_COLOR_DEPTH": "DEPTH_24_BIT",
    }
    env.pop("NO_COLOR", None)
    output = StringIO()
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM.replace("BACKGROUND", repr(background))],
        cwd=tmp_path,
        encoding="utf-8",
        dimensions=(24, width),
        env=env,
        timeout=10,
    )
    child.logfile_read = output
    try:
        child.expect("last")
        # Drain the frame, including its padding and footer after the cursor text.
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            with suppress(pexpect.TIMEOUT):
                child.read_nonblocking(65536, timeout=0.05)
        screen = pyte.Screen(width, 24)
        display = re.sub(r"\x1b\[(?:>1u|<u|>4;[02]m)", "", output.getvalue())
        pyte.Stream(screen).feed(display)
        first = next(i for i, row in enumerate(screen.display) if "first" in row)
        last = next(i for i, row in enumerate(screen.display) if "last" in row)
        rule = next(i for i, row in enumerate(screen.display) if "Worked for" in row)
        assert first - rule >= 3  # neutral gap, surface top padding, then text
        assert last - first >= 2
        for row in range(first - 1, last + 2):
            assert all(screen.buffer[row][col].bg == expected for col in range(width))
        assert all(screen.buffer[first - 2][col].bg == "default" for col in range(width))
        assert all(screen.buffer[last + 2][col].bg == "default" for col in range(width))
        child.send("\r")
        child.expect("CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
