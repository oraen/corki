import asyncio
import io
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import terminal
from corki.cli.display_text import sanitize_user_text
from corki.cli.input_owner import input_image_kwargs
from corki.config import CorkiSettings
from corki.protocol.tools import ImageAttachment


@pytest.mark.parametrize(
    "text,expected",
    [
        ("clean\ttext\n", "clean\ttext\n"),
        ("\x07before", "before"),
        ("before\x07", "before"),
        ("\x1b[31mbefore", "before"),
        ("before\x1b[31m", "before"),
        ("before\x1b[31", "before"),
        ("\x07[31m", "[31m"),
        ("\x07", ""),
        ("before\x1b[31mafter\x07", "beforeafter"),
        ("é\x85中", "é中"),
        ("before\x1bafter", "beforeafter"),
        ("a\x1b[\n中文31mb", "ab"),
        ("a\x1b]0;title\x07b", "a]0;titleb"),
        ("a\x9bb", "ab"),
        ("字\u200d🙂\u2028", "字\u200d🙂\u2028"),
    ],
)
def test_codex_user_input_sanitizer_cases(text, expected):
    assert sanitize_user_text(text) == expected


@pytest.mark.parametrize("length", [1000, 1001])
def test_clean_before_fold_submit_and_image_position_tracking(tmp_path, monkeypatch, length):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=io.StringIO())
        )
        payload = "中" * (length - 3) + "\n\t尾"
        pasted = "\x1b[31m" + payload.replace("\n", "\r\n") + "\x1b[0m\x07"

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(5):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    # Owned fixture image, never access the user's clipboard.
                    draft = ui._inline_images
                    draft.attach(ImageAttachment("data:image/png;base64,fixture"), 0, 0)
                    ui._set_image_document(0)
                    pipe.send_text("\x1b[200~" + pasted + "\x1b[201~")
                    while draft.expanded()[0] != payload + "[Image #1]":
                        await asyncio.sleep(0)
                    assert bool(draft.pastes) == (length > 1000)
                    assert draft.expanded()[2] == (length,)
                    assert not task.done()
                    pipe.send_text("\r")
                    value = await task
                    kwargs = input_image_kwargs(value)
                    assert kwargs["image_positions"] == (length,)
                    assert "\x1b" not in (tmp_path / "history").read_text()
                    assert ui._composer_paste.timer is None
                    assert not ui._composer_paste.state.active
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
