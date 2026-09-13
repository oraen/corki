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

from corki.cli import terminal
from corki.cli.input_owner import CycleModeInput
from corki.config import CorkiSettings


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
