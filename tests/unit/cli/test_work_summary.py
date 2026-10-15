import asyncio
from io import StringIO

import pytest
from rich.cells import cell_len
from rich.console import Console

from corki.cli import working_status
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    ToolCallCompleted,
    TurnCompleted,
)
from corki.protocol.ids import new_thread_id, new_turn_id


@pytest.mark.parametrize("width", [0, 1, 5, 12, 20, 40, 80, 160])
@pytest.mark.parametrize("seconds", [None, 0, 59, 60, 60.9, 61, 355, 7389])
def test_work_summary_is_one_exact_width_row(width, seconds):
    text = working_status.format_work_summary(seconds, width)
    assert cell_len(text) == width
    assert "\n" not in text
    if seconds is None or int(seconds) <= 60:
        assert text == "─" * width
    elif width >= 40:
        assert text.startswith(f"─ Worked for {working_status.format_elapsed(seconds)} ─")


@pytest.mark.parametrize("cancelled", [False, True])
def test_pending_work_summary_once_and_reflows_without_resetting_time(
    tmp_path, monkeypatch, cancelled
):
    now = [10.0]
    monkeypatch.setattr(working_status, "monotonic", lambda: now[0])
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=output, width=80, color_system=None),
    )
    app = CorkiApplication.__new__(CorkiApplication)
    app._ui = ui

    async def events():
        now[0] += 355
        if cancelled:
            raise asyncio.CancelledError
        thread, turn = new_thread_id(), new_turn_id()
        yield ToolCallCompleted(thread, turn, "call-1", "exec_command", False)
        yield TurnCompleted(thread, turn, "")
        yield TurnCompleted(thread, turn, "")

    async def scenario():
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await app._render_events(events())
        else:
            await app._render_events(events())
        assert ui._working_status.timer is None

    asyncio.run(scenario())
    text = output.getvalue()
    if cancelled:
        assert "Worked for" not in text
    else:
        assert text.count("Worked for 5m 55s") == 1
        now[0] += 600
        for width in (40, 100):
            replay = ui._transcript.render(width)
            row = next(row for row in replay.splitlines() if "Worked for" in row)
            assert "5m 55s" in row and cell_len(row) == width


@pytest.mark.parametrize("tool", [None, "update_plan", "request_user_input", "exec_command"])
@pytest.mark.parametrize("answer_mode", ["completed", "streamed", "fallback"])
def test_answer_consumes_work_separator_without_trailing_summary(
    tmp_path, monkeypatch, tool, answer_mode
):
    now = [10.0]
    monkeypatch.setattr(working_status, "monotonic", lambda: now[0])
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=StringIO(), width=80, color_system=None),
    )
    app = CorkiApplication.__new__(CorkiApplication)
    app._ui = ui
    thread, turn = new_thread_id(), new_turn_id()

    async def events():
        now[0] += 355
        if tool:
            yield ToolCallCompleted(thread, turn, "call-1", tool, False)
        if answer_mode == "streamed":
            yield AssistantTextDelta(thread, turn, "Answer")
        if answer_mode != "fallback":
            yield AssistantMessageCompleted(thread, turn, "Answer")
        yield TurnCompleted(thread, turn, "Answer")

    asyncio.run(app._render_events(events()))
    calls = ui._transcript.calls
    separators = [
        (i, args) for i, (fn, args, _) in enumerate(calls) if fn.__name__ == "show_work_duration"
    ]
    if tool == "exec_command":
        assert len(separators) == 1
        index, args = separators[0]
        assert args == (None,)
        assert calls[index + 1][0].__name__ == "show_assistant_message"
    else:
        assert not separators
    assert "Worked for" not in ui._transcript.render(80)
    assert ui._working_status.timer is None
