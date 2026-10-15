import asyncio
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.cells import cell_len

from corki.cli import terminal
from corki.cli.input_owner import CycleModeInput
from corki.config import CorkiSettings
from corki.protocol.events import HookRunSummary


@pytest.mark.parametrize("blocked", [None, "running", "history", "modal", "completion"])
def test_shift_tab_is_control_only_and_respects_input_focus(tmp_path, monkeypatch, blocked):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(working_directory=tmp_path), tmp_path / "history")
        ui.set_mode_cycle_enabled(blocked != "running")
        ui._transcript.modal_depth = int(blocked == "modal")
        ui._draft = "Keep this draft"

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                if blocked == "history":
                    ui._history_view.open()
                if blocked == "completion":
                    buffer = ui._session.default_buffer
                    buffer.complete_state = CompletionState(
                        buffer.document, [Completion(buffer.text, start_position=-len(buffer.text))]
                    )
                pipe.send_text("\x1b[Z")
                if blocked is None:
                    assert isinstance(await asyncio.wait_for(task, 3), CycleModeInput)
                else:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(task), 0.05)
                    assert ui._session.default_buffer.text == "Keep this draft"
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                assert ui._draft == "Keep this draft"
                assert not list(ui._session.history.get_strings())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


@pytest.mark.parametrize("width", [20, 40, 100])
@pytest.mark.parametrize("mode", ["default", "plan"])
@pytest.mark.parametrize("blocked", [None, "running", "history", "modal", "completion"])
def test_mode_hint_matches_keyboard_availability(tmp_path, monkeypatch, width, mode, blocked):
    class Output(DummyOutput):
        def get_size(self):
            return Size(rows=24, columns=width)

    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=Output())
        )
        ui = terminal.TerminalUI(CorkiSettings(working_directory=tmp_path), tmp_path / "history")
        ui.set_collaboration_mode(mode)
        ui.set_mode_cycle_enabled(blocked != "running")
        ui._transcript.modal_depth = int(blocked == "modal")
        if blocked == "history":
            ui._history_view.active = True
        if blocked == "completion":
            buffer = ui._session.default_buffer
            buffer.complete_state = CompletionState(buffer.document, [Completion("fixture")])
        text = fragment_list_to_text(to_formatted_text(ui._toolbar()))
        assert ("shift+tab to cycle" in text) is (blocked is None and width >= 40)
        if blocked is None:
            assert mode.capitalize() in text
            assert len(text) <= width


@pytest.mark.parametrize("width", [12, 20, 40])
@pytest.mark.parametrize("state", ["idle", "plan", "reasoning", "hook"])
def test_toolbar_fits_terminal_cells_with_chinese_status(tmp_path, monkeypatch, width, state):
    class Output(DummyOutput):
        def get_size(self):
            return Size(rows=24, columns=width)

    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=Output())
        )
        ui = terminal.TerminalUI(CorkiSettings(working_directory=tmp_path), tmp_path / "history")
        if state == "plan":
            ui.set_collaboration_mode("plan")
        elif state == "reasoning":
            ui._reasoning_active = True
            ui._reasoning_header = "正在分析中文状态信息"
        elif state == "hook":
            ui._hook_activity.start(
                HookRunSummary("fixture", "fixture", "Stop", "running", "正在检查中文项目文件"),
                0,
            )
            ui._hook_activity.advance(1)
        text = fragment_list_to_text(to_formatted_text(ui._toolbar()))
        assert cell_len(text) <= width
        assert "\n" not in text
        if state == "idle":
            assert text.strip() in {
                "?",
                "? help",
                "? help   ctrl+t history",
                "? for shortcuts   ctrl+t history",
                "? for shortcuts   ctrl+t history   ctrl+c to quit",
            }
        elif state == "plan":
            assert text.strip() in {"Plan mode", "Plan mode   ctrl+t history"}


def test_busy_toolbar_replaces_idle_help_and_restores_it(tmp_path):
    ui = terminal.TerminalUI(CorkiSettings(working_directory=tmp_path), tmp_path / "history")
    idle = fragment_list_to_text(to_formatted_text(ui._toolbar()))

    ui.set_turn_active(True)
    working = fragment_list_to_text(to_formatted_text(ui._toolbar()))
    assert "Working" in working and "? help" not in working

    ui.set_turn_active(False)
    assert fragment_list_to_text(to_formatted_text(ui._toolbar())) == idle


def test_tool_status_tracks_active_calls_and_clears_on_turn_end(tmp_path):
    ui = terminal.TerminalUI(CorkiSettings(working_directory=tmp_path), tmp_path / "history")
    ui.set_turn_active(True)

    ui.set_tool_activity("call-1", "read_file")
    assert "Running read_file" in fragment_list_to_text(to_formatted_text(ui._toolbar()))
    ui.set_tool_activity("call-2", "search")
    assert "Running 2 tools" in fragment_list_to_text(to_formatted_text(ui._toolbar()))
    ui.set_tool_activity("call-1", None)
    assert "Running search" in fragment_list_to_text(to_formatted_text(ui._toolbar()))
    ui.set_turn_active(False)
    assert "Running" not in fragment_list_to_text(to_formatted_text(ui._toolbar()))
    assert ui._active_tools == {}
