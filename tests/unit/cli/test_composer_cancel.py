import asyncio
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli import terminal
from corki.cli.input_owner import InputInterrupted, InputOwner
from corki.config import CorkiSettings


def test_ctrl_c_clears_draft_without_submitting_or_closing_and_up_restores(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
        ui._draft = "草稿 draft\nsecond line"

        async def scenario():
            task = asyncio.create_task(InputOwner(ui).read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\x03")
                    while ui._session.default_buffer.text and not task.done():
                        await asyncio.sleep(0)
                    assert not task.done(), "Ctrl+C must clear the draft, not exit the composer"
                    assert ui._draft == ""
                    assert not ui._transcript.calls
                    # Reset reloads FileHistory on the next rendered frame.
                    while (
                        "草稿 draft\nsecond line" not in ui._session.default_buffer._working_lines
                    ):
                        await asyncio.sleep(0)
                    pipe.send_text("\x1b[A\r")
                    assert await task == "草稿 draft\nsecond line"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


@pytest.mark.parametrize("draft", ["", "草稿 draft\nsecond line"])
def test_escape_interrupts_work_and_preserves_draft(tmp_path, monkeypatch, draft):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
        ui._draft = draft

        async def scenario():
            owner = InputOwner(ui)
            ui.set_turn_active(True)
            task = asyncio.create_task(owner.read_message())
            try:
                async with asyncio.timeout(5):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\x1b")
                    with pytest.raises(InputInterrupted):
                        await task
                    assert ui._draft == draft
                    assert owner._reader is None
                    assert not ui._transcript.calls
                    ui.set_turn_active(False)
                    assert ui._working_status.timer is None
                    task = asyncio.create_task(owner.read_message())
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("followup")
                    await asyncio.sleep(0.15)
                    pipe.send_text("\r")
                    assert await task == draft + "followup"
            finally:
                ui.set_turn_active(False)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


@pytest.mark.parametrize("case", ["idle", "completion", "history", "alt_enter"])
def test_escape_respects_local_controls(tmp_path, monkeypatch, case):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")

        async def scenario():
            ui.set_turn_active(case != "idle")
            task = asyncio.create_task(InputOwner(ui).read_message())
            try:
                async with asyncio.timeout(5):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    if case == "completion":
                        pipe.send_text("/sta")
                        while ui._session.default_buffer.complete_state is None:
                            await asyncio.sleep(0)
                    elif case == "history":
                        ui._history_view.open()
                    pipe.send_text("\x1b\r" if case == "alt_enter" else "\x1b")
                    # Bare Escape requires terminal and chord disambiguation.
                    await asyncio.sleep(0.8)
                    assert not task.done()
                    assert not ui._history_view.active
                    assert ui._session.default_buffer.complete_state is None
                    if case == "alt_enter":
                        assert ui._session.default_buffer.text == "\n"
                    elif case == "completion":
                        assert ui._session.default_buffer.text == "/sta"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                ui.set_turn_active(False)

        asyncio.run(scenario())
