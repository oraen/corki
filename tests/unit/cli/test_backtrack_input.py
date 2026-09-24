import asyncio
import io
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import terminal
from corki.cli.input_owner import BacktrackInput
from corki.config import CorkiSettings
from corki.protocol.items import UserMessageItem


def test_double_escape_requests_edit_without_submitting(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\x1b")
                    while not ui._backtrack_primed:
                        await asyncio.sleep(0)
                    assert not task.done()
                    pipe.send_text("\x1b")
                    assert isinstance(await task, BacktrackInput)
                    assert not ui._transcript.calls
                    assert not (tmp_path / "history").exists()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


@pytest.mark.parametrize("has_history", [False, True])
def test_escape_hint_requires_history_and_clears_on_input(tmp_path, monkeypatch, has_history):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )
        if has_history:
            ui.replay_history((UserMessageItem("earlier", "turn"),))

        def hint_visible():
            return "edit previous message" in "".join(text for _, text in ui._session_status())

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(5):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\x1b")
                    while not ui._backtrack_primed:
                        await asyncio.sleep(0)
                    assert hint_visible() == has_history
                    pipe.send_text("中文")
                    while ui._session.default_buffer.text != "中文":
                        await asyncio.sleep(0)
                    assert not hint_visible()
                    assert not ui._backtrack_primed
                    assert not task.done()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
