import asyncio
import io
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import composer_paste, terminal
from corki.config import CorkiSettings


@pytest.mark.parametrize("burst_limit", [None, 1024])
@pytest.mark.parametrize(
    "text", ["hello\rworld", "中 文\r" + "字" * 1100], ids=["ascii", "long-cjk"]
)
def test_raw_paste_enter_does_not_submit(tmp_path, monkeypatch, text, burst_limit):
    # Match Codex's handle_input_basic_with_time tests: a burst is defined by
    # event times, not how much CPU an xdist worker receives between characters.
    clock = [0.0]
    monkeypatch.setattr(composer_paste, "monotonic", lambda: clock[0])
    if burst_limit is not None:
        monkeypatch.setattr(composer_paste, "MAX_USER_INPUT_TEXT_CHARS", burst_limit)
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
                async with asyncio.timeout(5):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text(text)
                    burst = ui._composer_paste
                    while len(ui._inline_images.expanded()[0]) + len(burst.state.buffer) + (
                        1 if burst.state.pending is not None else 0
                    ) < len(text):
                        await asyncio.sleep(0)
                    clock[0] = 0.3
                    if burst_limit is not None:
                        assert len(burst.state.buffer) < burst_limit
                    burst.cancel_timer()
                    burst.flush()
                    assert not task.done()
                    assert ui._inline_images.expanded()[0] == text.replace("\r", "\n")
                    if len(text) > 1000:
                        assert ui._inline_images.pastes
                    pipe.send_text("\r")
                    assert await task == text.replace("\r", "\n")
                    assert ui._composer_paste.timer is None
                    assert not ui._composer_paste.state.active
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_pending_ascii_survives_cancel_without_timer(tmp_path, monkeypatch):
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
                    pipe.send_text("a")
                    while not ui._composer_paste.state.active:
                        await asyncio.sleep(0)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    assert ui._draft == "a"
                    assert ui._composer_paste.timer is None
                    assert not ui._composer_paste.state.active
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
