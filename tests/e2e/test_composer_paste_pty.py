"""A multiline Chinese paste remains one editable, explicitly submitted message."""

import os
import sys
from io import StringIO

import pexpect
import pyte
import pytest
from wcwidth import wcswidth

PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings

async def main():
    cwd = Path.cwd()
    ui = TerminalUI(CorkiSettings(cwd), cwd / "input-history")
    message = await ui.read_message()
    assert message == "请检查中文文件与布局\n第二行包含 emoji 😀 和结尾标记 END", repr(message)
    assert list(ui._session.history.get_strings()) == [message]
    print("PASTE_SUBMITTED_ONCE", flush=True)
asyncio.run(main())
"""


CONTROL_PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings

async def main():
    cwd = Path.cwd()
    ui = TerminalUI(CorkiSettings(cwd), cwd / "input-history")
    message = await ui.read_message()
    assert message == "draft" + chr(27) + "[2Jtext", repr(message)
    replay = ui._transcript.render(40)
    assert r"\x1b[2J" in replay and chr(27) + "[2J" not in replay, repr(replay)
    print("PASTED_CONTROL_PRESERVED_AS_TEXT", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_multiline_chinese_paste_survives_resize_and_requires_enter(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=10,
        dimensions=(24, width),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    output = StringIO()
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        child.send("\x1b[200~请检查中文文件与布局\n第二行包含 emoji 😀 和结尾标记 END\x1b[201~")
        child.expect("END")
        assert child.expect(["PASTE_SUBMITTED_ONCE", pexpect.TIMEOUT], timeout=0.2) == 1

        screen = pyte.Screen(width, 24)
        stream = pyte.Stream(screen)
        stream.feed(output.getvalue())
        visible = "\n".join(screen.display)
        assert "请检查中文文件与布局" in visible
        assert "第二行包含 emoji 😀 和结尾标记 END" in visible
        assert all(wcswidth(row) <= width for row in screen.display)

        offset = len(output.getvalue())
        resized = 100 if width == 40 else 40
        child.setwinsize(24, resized)
        child.expect("END")
        screen.resize(lines=24, columns=resized)
        stream.feed(output.getvalue()[offset:])
        visible = "\n".join(screen.display)
        assert "请检查中文文件与布局" in visible
        assert "第二行包含 emoji 😀 和结尾标记 END" in visible
        assert all(wcswidth(row) <= resized for row in screen.display)

        child.send("\r")
        child.expect("PASTE_SUBMITTED_ONCE")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


@pytest.mark.parametrize("width", [40, 100])
def test_pasted_control_text_cannot_clear_composer_or_history(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", CONTROL_PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=10,
        dimensions=(24, width),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    output = StringIO()
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        child.send("\x1b[200~draft\x1b[2Jtext\x1b[201~")
        child.expect("text")
        assert "\x1b[2J" not in output.getvalue()
        child.send("\r")
        child.expect_exact("PASTED_CONTROL_PRESERVED_AS_TEXT")
        assert "\x1b[2J" not in output.getvalue()
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
