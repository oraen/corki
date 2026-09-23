import asyncio

import pytest
from prompt_toolkit.formatted_text import fragment_list_to_text
from rich.cells import cell_len

from corki.cli import working_status
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings


def test_turn_time_survives_tools_and_nested_modals_and_resets(tmp_path, monkeypatch):
    now = [10.0]
    monkeypatch.setattr(working_status, "monotonic", lambda: now[0])
    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
    ui.set_turn_active(True)
    now[0] += 61
    ui.set_tool_activity("call", "read_file")
    ui.set_turn_active(True)
    assert "1m 01s" in fragment_list_to_text(ui._toolbar())
    ui._transcript.modal_depth += 1
    now[0] += 90
    ui._transcript.modal_depth += 1
    ui._transcript.modal_depth -= 1
    assert ui._working_status.elapsed == 61
    ui._transcript.modal_depth -= 1
    now[0] += 2
    ui.set_tool_activity("call", None)
    assert ui._working_status.elapsed == 63
    ui.set_turn_active(False)
    assert "Working" not in fragment_list_to_text(ui._toolbar())
    assert ui._working_status.timer is None
    ui.set_turn_active(True)
    assert ui._working_status.elapsed == 0
    ui.set_turn_active(False)


@pytest.mark.parametrize("width", [0, 5, 12, 20, 40, 100])
def test_status_preserves_time_and_fits_unicode_details(width, monkeypatch):
    monkeypatch.setattr(working_status, "monotonic", lambda: 0.0)
    status = working_status.WorkingStatus(lambda: None, animated=False)
    status.set_active(True)
    status.accumulated = 7389
    fragments = status.fragments(width, detail="正在读取文件\x1b[2J\n详情" * 10)
    text = fragment_list_to_text(fragments)
    assert all(cell_len(line) <= width for line in text.splitlines())
    assert "\x1b" not in text
    if width >= 20:
        assert "2h 03m 09s" in text
    if width >= 40:
        assert any("bold" in style and "Working" in value for style, value in fragments)


def test_quiet_turn_refreshes_and_stops_its_timer():
    async def scenario():
        refreshed = asyncio.Event()
        status = working_status.WorkingStatus(refreshed.set)
        status.set_active(True)
        refreshed.clear()
        await asyncio.wait_for(refreshed.wait(), 1)
        assert status.elapsed >= 0.2
        timer = status.timer
        status.set_active(False)
        assert timer.cancelled() and status.timer is None

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
def test_event_stream_terminal_paths_remove_status_and_timer(tmp_path, failure):
    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
    app = CorkiApplication.__new__(CorkiApplication)
    app._ui = ui

    async def events():
        assert ui._working_status.active
        assert ui._working_status.timer is not None
        if failure:
            raise failure()
        if False:
            yield

    async def scenario():
        if failure:
            with pytest.raises(failure):
                await app._render_events(events())
        else:
            await app._render_events(events())
        assert not ui._working_status.active
        assert ui._working_status.timer is None
        assert "Working" not in fragment_list_to_text(ui._toolbar())

    asyncio.run(scenario())
