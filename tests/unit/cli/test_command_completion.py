import asyncio
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli import terminal
from corki.config import CorkiSettings


@pytest.mark.parametrize(
    "key,command",
    [("\x1b[B", "/memory"), ("\x0e", "/memory"), ("\x1b[A", "/mcp"), ("\x10", "/mcp")],
)
def test_candidate_navigation_preserves_draft(tmp_path, monkeypatch, key, command):
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
                    pipe.send_text("/m")
                    buffer = ui._session.default_buffer
                    while buffer.complete_state is None:
                        await asyncio.sleep(0)
                    pipe.send_text(key)
                    await asyncio.sleep(0.05)
                    assert buffer.text == "/m"
                    assert buffer.complete_state.current_completion.text == command
                    assert not list(ui._session.history.get_strings())
                    pipe.send_text("\t")
                    while buffer.text != command + " " and not task.done():
                        await asyncio.sleep(0)
                    assert not task.done()
                    pipe.send_text("argument")
                    await asyncio.sleep(0.15)
                    pipe.send_text("\r")
                    assert await task == command + " argument"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_tab_completes_command_without_submission(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
        ui._draft = "/sta"

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\t")
                    while ui._session.default_buffer.text != "/status " and not task.done():
                        await asyncio.sleep(0)
                    assert not task.done()
                    assert not list(ui._session.history.get_strings())
                    pipe.send_text("\r")
                    assert await task == "/status "
                    assert list(ui._session.history.get_strings()) == ["/status "]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


@pytest.mark.parametrize("action", ["edit_after_escape", "alt_enter"])
def test_completion_keeps_editing_and_multiline_available(tmp_path, monkeypatch, action):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
        ui._session.app.ttimeoutlen = 0.01
        ui._session.app.timeoutlen = 0.01

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("/sta")
                    buffer = ui._session.default_buffer
                    while buffer.complete_state is None:
                        await asyncio.sleep(0)
                    if action == "alt_enter":
                        pipe.send_text("\x1b\r")
                        while buffer.text != "/sta\n" and not task.done():
                            await asyncio.sleep(0)
                        assert not task.done()
                        assert buffer.text == "/sta\n"
                        pipe.send_text("argument")
                        await asyncio.sleep(0.15)
                        pipe.send_text("\r")
                        assert await task == "/sta\nargument"
                    else:
                        pipe.send_text("\x1b")
                        await asyncio.sleep(0.05)
                        assert buffer.complete_state is None
                        pipe.send_text("t")
                        while buffer.complete_state is None:
                            await asyncio.sleep(0)
                        pipe.send_text("\t")
                        while buffer.text != "/status " and not task.done():
                            await asyncio.sleep(0)
                        assert not task.done()
                        pipe.send_text("\r")
                        assert await task == "/status "
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())


def test_escape_dismisses_candidate_without_enter_reselecting_it(tmp_path, monkeypatch):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
        ui._session.app.ttimeoutlen = 0.01
        ui._session.app.timeoutlen = 0.01

        async def scenario():
            task = asyncio.create_task(ui.read_message())
            try:
                async with asyncio.timeout(3):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("/sta")
                    buffer = ui._session.default_buffer
                    while buffer.complete_state is None:
                        await asyncio.sleep(0)
                    pipe.send_text("\x1b")
                    await asyncio.sleep(0.05)
                    assert buffer.complete_state is None
                    assert buffer.text == "/sta"
                    assert not task.done()
                    pipe.send_text("\r")
                    assert await task == "/sta"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
