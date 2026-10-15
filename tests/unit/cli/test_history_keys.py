import asyncio
from functools import partial
from io import StringIO

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import terminal
from corki.config import CorkiSettings


@pytest.mark.parametrize(
    "key,delta",
    [("j", 1), ("k", -1), (" ", 20), ("\x06", 20), ("\x02", -20), ("\x04", 10), ("\x15", -10)],
)
def test_history_navigation_keys_do_not_edit_or_submit_draft(tmp_path, monkeypatch, key, delta):
    class Output(DummyOutput):
        def get_size(self):
            return Size(rows=24, columns=80)

    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=Output())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        ui.show_assistant_message("\n\n".join(f"entry {i}" for i in range(100)))
        ui._draft = "保留 draft"

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    view = ui._history_view
                    view.open()
                    view.row = 50
                    pipe.send_text(key + "q\r")
                    assert await task == "保留 draft"
                    assert view.row == 50 + delta
                    assert not view.active
                    assert list(ui._session.history.get_strings()) == ["保留 draft"]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
