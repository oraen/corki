"""History navigation shares input ownership and cannot submit a hidden draft."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings

async def main():
    cwd = Path.cwd()
    ui = TerminalUI(CorkiSettings(cwd), cwd / "input-history")
    ui.show_assistant_message("\n\n".join(f"HISTORY_ROW_{i:03}" for i in range(60)))
    message = await ui.read_message()
    assert message == "retained draft", repr(message)
    assert not ui._history_view.active
    assert list(ui._session.history.get_strings()) == ["retained draft"]
    print("HISTORY_INPUT_VERIFIED", flush=True)
asyncio.run(main())
"""

PAGED_PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.sessions import TurnRecord, TurnStatus

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    class Model:
        async def stream(self, request):
            raise AssertionError("history browsing must not sample")
            yield
        async def aclose(self):
            pass
    runtime = await LangGraphRuntime.acreate(
        settings=settings, database_path=cwd / "history.db", model=Model(), home_path=cwd,
    )
    await runtime._ensure_ready()
    for index in range(130):
        turn = new_turn_id()
        await runtime._repository.save_turn(
            TurnRecord(turn, runtime.thread_id, TurnStatus.COMPLETED, ""),
        )
        await runtime._repository.append_items(
            runtime.thread_id,
            (UserMessageItem(
                ("ΩΩΩΩΩΩΩΩΩΩΩΩ " if index == 0 else "") + f"PAGED_ENTRY_{index:03}", turn,
            ),),
        )
    class UI(TerminalUI):
        async def read_message(self):
            pager = self._history_view.loader
            assert pager.has_older and len(pager.items) == 100
            assert "PAGED_ENTRY_000" not in self._transcript.render(80)
            assert await super().read_message() == "retained draft"
            assert not pager.has_older and pager.task is None
            assert len(pager.items) == 130
            assert self._transcript.render(80).count("PAGED_ENTRY_000") == 1
            assert list(self._session.history.get_strings()) == ["retained draft"]
            raise EOFError
    ui = UI(settings, cwd / "input-history")
    assert await CorkiApplication(settings, CorkiPaths.from_home(cwd), runtime, ui).run() == 0
    assert ui._history_view.loader.closed
    print("PAGED_HISTORY_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_home_loads_persisted_older_history_and_preserves_draft(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PAGED_PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=20,
        dimensions=(24, width),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    try:
        child.expect("Ask Corki to do anything")
        assert "PAGED_ENTRY_000" not in child.before
        child.send("retained draft\x14")
        child.expect_exact("\x1b[?1049h")
        child.expect("PAGED_ENTRY_129")
        child.send("\x1b[H")
        # Screen diffs may reuse PAGED_ENTRY_ from the previous page. The
        # distinct oldest marker must actually be emitted when that page loads.
        child.expect("ΩΩΩΩΩΩΩΩΩΩΩΩ")
        child.send("q")
        child.expect_exact("\x1b[?1049l")
        child.expect("retained draft")
        child.send("\r")
        child.expect("PAGED_HISTORY_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


LIVE_PROGRAM = r"""
import asyncio
import sys
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings
import corki.cli.history_view as history_view

async def main():
    if sys.argv[1] == "overflow":
        history_view.MAX_DEFERRED_OUTPUT_CHARS = 1
    cwd = Path.cwd()
    ui = TerminalUI(CorkiSettings(cwd), cwd / "input-history")
    async def produce():
        while not ui._session.app.renderer._in_alternate_screen:
            await asyncio.sleep(0.01)
        ui.show_notice("LIVE_NOTICE")
        ui.begin_assistant_message()
        answer = "LIVE_ANSWER" + ("\n\n" if sys.argv[1] == "stream" else "")
        await ui.append_assistant_delta_live(answer)
        await ui.complete_assistant_message(answer)
        if sys.argv[1] == "overflow":
            assert ui._history_view._deferred_overflow
            assert not ui._history_view.deferred_output
            assert ui._transcript.repair_pending
        ui._transcript.repair_pending = True
        await ui._transcript.repair()
        assert ui._session.app.renderer._in_alternate_screen
    producer = asyncio.create_task(produce())
    # Application.run owns this watcher in production; this focused fixture
    # runs TerminalUI directly, so mirror its startup and joined shutdown.
    watcher = asyncio.create_task(ui.watch_resize()) if sys.argv[1] == "overflow" else None
    try:
        assert await ui.read_message() == "retained draft"
    finally:
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
    await producer
    assert not ui._history_view.deferred_output
    assert not ui._session.app.renderer._in_alternate_screen
    print("LIVE_HISTORY_VERIFIED", flush=True)
asyncio.run(main())
"""

RESIZE_PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings

async def main():
    cwd = Path.cwd()
    ui = TerminalUI(CorkiSettings(cwd), cwd / "input-history")
    ui.show_assistant_message("ΩΩΩΩΩΩΩΩΩΩΩΩ\n\n" + "\n\n".join(
        f"RESIZE_ROW_{i:03} " + "wrapping content " * 8 for i in range(60)
    ))
    app, view = ui._session.app, ui._history_view
    initial_width = app.output.get_size().columns
    async def verify_resize():
        while not app.renderer._in_alternate_screen:
            await asyncio.sleep(0.01)
        while app.output.get_size().columns == initial_width:
            await asyncio.sleep(0.01)
        view.text()
        assert view.row == view.rows - 1
        ui.show_notice("RESIZE_TAIL_VERIFIED")
        while view.row != 0:
            await asyncio.sleep(0.01)
        while app.output.get_size().columns != initial_width:
            await asyncio.sleep(0.01)
        view.text()
        assert view.row == 0
        assert app.renderer._in_alternate_screen
        assert ui._session.default_buffer.text == "retained draft"
    verifier = asyncio.create_task(verify_resize())
    assert await ui.read_message() == "retained draft"
    await asyncio.wait_for(verifier, 3)
    assert not view.active
    assert list(ui._session.history.get_strings()) == ["retained draft"]
    print("RESIZE_HISTORY_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width, resized", [(40, 100), (100, 40)])
def test_history_resize_keeps_tail_and_manual_top_without_losing_draft(tmp_path, width, resized):
    child = pexpect.spawn(
        sys.executable,
        ["-c", RESIZE_PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(24, width),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("retained draft\x14")
        child.expect_exact("\x1b[?1049h")
        child.expect("RESIZE_ROW_059")
        child.setwinsize(18, resized)
        child.expect("RESIZE_TAIL_VERIFIED")
        child.send("\x1b[H")
        child.expect("ΩΩΩΩΩΩΩΩΩΩΩΩ")
        child.setwinsize(30, width)
        child.expect("ΩΩΩΩΩΩΩΩΩΩΩΩ")
        child.send("q")
        child.expect_exact("\x1b[?1049l")
        child.expect("retained draft")
        child.send("\r")
        child.expect("RESIZE_HISTORY_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("mode", ["stream", "short", "overflow"])
def test_live_output_does_not_leave_history_screen_until_close(tmp_path, width, mode):
    child = pexpect.spawn(
        sys.executable,
        ["-c", LIVE_PROGRAM, mode],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(24, width),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("retained draft\x14")
        child.expect_exact("\x1b[?1049h")
        assert child.expect(["LIVE_ANSWER", "\x1b\\[\\?1049l"]) == 0
        assert "LIVE_NOTICE" in child.before
        assert child.expect(["\x1b\\[\\?1049l", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("q")
        child.expect_exact("\x1b[?1049l")
        if mode == "overflow":
            # The bounded replay queue asks the existing resize watcher to
            # rebuild canonical output after the inline composer is restored.
            child.expect_exact("\x1b[3J\x1b[2J\x1b[H")
        child.expect("retained draft")
        assert child.before.count("LIVE_NOTICE") == 1
        assert child.before.count("LIVE_ANSWER") == 1
        child.send("\r")
        child.expect("LIVE_HISTORY_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("close_key", ["q", "\x1b", "\x14", "\x03", "navigation"])
def test_history_scroll_and_close_preserves_draft(tmp_path, width, close_key):
    program = PROGRAM
    if close_key == "navigation":
        program = program.replace(
            '    print("HISTORY_INPUT_VERIFIED", flush=True)',
            "    assert ui._history_view.row == 11, ui._history_view.row\n"
            '    print("HISTORY_INPUT_VERIFIED", flush=True)',
        )
    child = pexpect.spawn(
        sys.executable,
        ["-c", program],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(24, width),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("retained draft")
        child.sendcontrol("t")
        child.expect_exact("\x1b[?1049h")
        child.expect("Esc to close")
        child.expect("HISTORY_ROW_059")
        child.send("\x1b[H")
        child.expect("HISTORY_ROW_000")
        if close_key == "navigation":
            # Half-page down/up, page down/up, then down a page, up half and one line.
            # Closing afterward must preserve both the scroll position and composer draft.
            child.send("\x04\x15\x06\x02 \x15jq")
            child.expect_exact("\x1b[?1049l")
            child.expect("retained draft")
            child.send("\r")
            child.expect("HISTORY_INPUT_VERIFIED")
            child.expect(pexpect.EOF)
            child.close()
            assert child.exitstatus == 0
            return
        child.send("\x1b[6~")
        child.send("xyz\r\t")
        child.send("\x1b[200~pasted\nnot a submission\x1b[201~")
        child.send(close_key)
        child.expect_exact("\x1b[?1049l")
        child.expect("retained draft")
        child.send("\r")
        child.expect("HISTORY_INPUT_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
