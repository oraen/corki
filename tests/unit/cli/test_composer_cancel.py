import asyncio
from functools import partial

from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli import terminal
from corki.cli.input_owner import InputOwner
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
