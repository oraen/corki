import asyncio
from functools import partial
from io import StringIO

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output

from corki.cli import terminal
from corki.cli.approval import choose_approval
from corki.cli.terminal_responses import TerminalResponseParser
from corki.config import CorkiSettings


@pytest.mark.parametrize("sequence", ["\x1b[13;2u", "\x1b[27;2;13~", "\x1b[13;2:1u"])
def test_shift_enter_edits_multiline_without_submitting(tmp_path, monkeypatch, sequence):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("first" + sequence + "second")
                    while ui._session.default_buffer.text != "first\nsecond":
                        assert not task.done(), "modified Enter submitted the composer"
                        await asyncio.sleep(0)
                    pipe.send_text("\r")
                    assert await task == "first\nsecond"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_fragmented_enhanced_keys_releases_controls_and_paste():
    events = []
    parser = TerminalResponseParser(Vt100Parser(events.append))
    for char in "\x1b[13;2u\x1b[13;2:3u\x1b[99;5u\x1b[9;2u\x1b[97;2u":
        parser.feed(char)
        if char != "\x1b":
            parser.flush()
    assert [event.key for event in events] == [Keys.ControlJ, Keys.ControlC, Keys.BackTab, "A"]
    events.clear()
    parser.feed("\x1b[200~literal\x1b[13;2u\x1b[201~")
    assert [(event.key, event.data) for event in events] == [
        (Keys.BracketedPaste, "literal\x1b[13;2u")
    ]


def test_modified_enter_cannot_accept_approval():
    with create_pipe_input() as pipe:

        async def scenario():
            session = PromptSession(input=pipe, output=DummyOutput())
            task = asyncio.create_task(choose_approval(session, execution=True))
            try:
                async with asyncio.timeout(3):
                    while not session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\x1b[13;2u\x1b[13;2:3u")
                    await asyncio.sleep(0.05)
                    assert not task.done()
                    pipe.send_text("\x1b[13u")
                    assert await task == "accept"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_cancelled_composer_restores_reporting_and_preserves_draft(tmp_path, monkeypatch):
    output_text = StringIO()
    output = Vt100_Output(output_text, lambda: Size(rows=24, columns=80), enable_cpr=False)
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=output)
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while "\x1b[>1u" not in output_text.getvalue():
                        await asyncio.sleep(0)
                    pipe.send_text("unfinished")
                    while ui._session.default_buffer.text != "unfinished":
                        await asyncio.sleep(0)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert ui._draft == "unfinished"
                    transcript = output_text.getvalue()
                    assert transcript.count("\x1b[>1u") == 1
                    assert transcript.count("\x1b[<u\x1b[>4;0m") == 1
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
