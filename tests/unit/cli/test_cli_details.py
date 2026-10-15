import asyncio
from functools import partial
from io import StringIO
from pathlib import Path

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import terminal
from corki.cli.terminal_title import project_title
from corki.cli.transcript import Transcript
from corki.config import CorkiSettings


@pytest.mark.parametrize("tty", [True, False])
def test_title_is_sanitized_tty_only_and_cleared_on_error(tty):
    output = StringIO()
    console = Console(file=output, force_terminal=tty)
    with (
        pytest.raises(RuntimeError),
        project_title(console, Path("/project/测试\x07\x1b\u202ename")),
    ):
        raise RuntimeError
    assert output.getvalue() == ("\x1b]0;测试name\x07\x1b]0;\x07" if tty else "")


@pytest.mark.parametrize("width", [20, 40, 100])
def test_streamed_tool_preview_is_bounded_and_history_is_complete(width):
    ui = terminal.TerminalUI.__new__(terminal.TerminalUI)
    output = StringIO()
    ui._console = Console(file=output, width=width, color_system=None)
    ui._transcript = Transcript(ui)
    for index in range(30):
        ui.show_tool_output_chunk("a", f"row-{index:02}\n")
    assert len(ui._tool_previews["a"][1]) <= 3
    # Replay while running must not overwrite the live preview budget.
    before = ui._tool_previews.copy()
    ui._transcript.render(width)
    assert ui._tool_previews == before
    ui.show_tool_output_chunk("a", "", finished=True)
    assert not ui._tool_previews
    assert len(output.getvalue().splitlines()) == 5
    assert "row-00" in output.getvalue() and "row-29" in output.getvalue()
    assert "row-15" not in output.getvalue()
    history = ui._transcript.render(width, expand_tools=True)
    assert all(f"row-{index:02}" in history for index in range(30))
    assert "ctrl+t" not in history


def test_single_long_line_and_command_header_are_screen_row_bounded():
    ui = terminal.TerminalUI.__new__(terminal.TerminalUI)
    output = StringIO()
    ui._console = Console(file=output, width=40, color_system=None)
    ui._transcript = Transcript(ui)
    ui.show_tool_started("exec_command", "中" * 300)
    assert len(output.getvalue().splitlines()) <= 2
    output.seek(0)
    output.truncate()
    ui.show_tool_output("中" * 1000)
    assert len(output.getvalue().splitlines()) == 5
    assert ui._transcript.calls[-1][1][0] == "中" * 1000


@pytest.mark.parametrize("interrupted", [True, False])
def test_interleaved_tools_release_preview_state_on_all_terminal_paths(tmp_path, interrupted):
    from corki.cli.application import CorkiApplication
    from corki.protocol.events import ToolCallCompleted, ToolCallStarted, ToolOutputDelta
    from corki.protocol.ids import new_thread_id, new_turn_id

    ui = terminal.TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=StringIO(), width=40, color_system=None),
    )
    app = CorkiApplication.__new__(CorkiApplication)
    app._ui = ui
    thread, turn = new_thread_id(), new_turn_id()

    async def events():
        for call in ("a", "b"):
            yield ToolCallStarted(thread, turn, call, "exec_command", "test")
        for index in range(20):
            for call in ("a", "b"):
                yield ToolOutputDelta(thread, turn, call, f"{call}-{index:02}\n")
        if interrupted:
            raise asyncio.CancelledError
        for call in ("a", "b"):
            yield ToolCallCompleted(thread, turn, call, "exec_command", False)

    async def scenario():
        if interrupted:
            with pytest.raises(asyncio.CancelledError):
                await app._render_events(events())
        else:
            await app._render_events(events())
        assert not ui._tool_previews
        assert ui._working_status.timer is None
        history = ui._transcript.render(40, expand_tools=True)
        assert "a-10" in history and "b-10" in history

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "key,expected",
    [
        ("\x01", "abc\nXone two"),
        ("\x01\x05", "abc\none twoX"),
        ("\x02", "abc\none twXo"),
        ("\x02\x06", "abc\none twoX"),
        ("\x1b[1;5D", "abc\none Xtwo"),
        ("\x1b[1;5D\x1b[1;5C", "abc\none twoX"),
        ("\x1b[97;5u", "abc\nXone two"),
    ],
)
def test_control_cursor_keys_preserve_multiline_input(tmp_path, monkeypatch, key, expected):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
        ui._draft = "abc\none two"

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text(key + "X")
                    await asyncio.sleep(0.15)  # intentional submit, not a pasted newline
                    pipe.send_text("\r")
                    assert await task == expected
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
