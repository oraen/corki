import asyncio
import base64
from functools import partial
from io import StringIO

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from corki.cli import clipboard, terminal
from corki.cli.commands import CommandAction, CommandDispatcher
from corki.cli.input_owner import InputOwner
from corki.config import CorkiPaths, CorkiSettings


def test_dispatch_and_targets_preserve_markdown_and_code(tmp_path):
    dispatcher = CommandDispatcher(CorkiSettings(tmp_path), CorkiPaths.from_home(tmp_path))
    assert dispatcher.dispatch("/copy").action is CommandAction.COPY
    assert dispatcher.dispatch("/copy extra").output == "Usage: /copy"
    markdown = "Hello\n\n```python\nprint('中文')\n```\n\n> quoted\n> text\n"
    assert list(clipboard.copy_choices(markdown).values()) == [
        markdown,
        "print('中文')\n",
        "quoted\ntext\n",
    ]


@pytest.mark.parametrize(
    "keys,expected", [("\r", "whole"), ("\x1b[B\r", "code"), ("\x1b", None), ("cancel-task", None)]
)
def test_copy_picker_owns_input_and_preserves_draft(tmp_path, monkeypatch, keys, expected):
    copied = []
    markdown = "Response\n\n```text\ncopy me\n```"

    async def write(text, **kwargs):
        copied.append(text)
        return "Copied to clipboard."

    monkeypatch.setattr(clipboard, "write_clipboard", write)
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        ui.show_assistant_message(markdown)
        ui.show_tool_output("not the assistant")
        ui.show_assistant_message("not a response", is_error=True)
        ui._draft = "unfinished input"

        async def scenario():
            owner = InputOwner(ui)
            task = asyncio.create_task(owner.copy_response())
            try:
                async with asyncio.timeout(3):
                    while not ui._form_session.app.is_running:
                        await asyncio.sleep(0)
                    if keys == "cancel-task":
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        pipe.send_text(keys)
                        await task
                assert ui._draft == "unfinished input"
                assert not ui._transcript.modal_depth
                assert ui._model_menu is None
                assert owner._ordinary.is_set() and not owner._modals
                assert not list(ui._form_session.history.get_strings())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
    assert copied == (
        [] if expected is None else [markdown if expected == "whole" else "copy me\n"]
    )


def test_no_response_does_not_open_menu_or_write_clipboard(tmp_path):
    output = StringIO()
    ui = terminal.TerminalUI(
        CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=output)
    )
    asyncio.run(ui.read_copy(None))
    assert "No agent response to copy" in output.getvalue()
    assert not ui._transcript.modal_depth


def test_native_clipboard_passes_literal_utf8_to_owned_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    calls = []

    async def run(argv, data, **kwargs):
        calls.append((argv, data, kwargs))
        return b""

    monkeypatch.setattr(clipboard, "run_owned", run)
    text = "中文 $(do-not-run) `literal`\n"
    result = asyncio.run(
        clipboard.write_clipboard(text, console=Console(file=StringIO()), cwd=tmp_path)
    )
    assert result == "Copied to clipboard."
    assert calls[0][:2] == (["/usr/bin/pbcopy"], text.encode())
    assert calls[0][2]["timeout"] == 3


@pytest.mark.parametrize("tmux", [False, True])
def test_ssh_uses_terminal_clipboard_without_native_process(tmp_path, monkeypatch, tmux):
    monkeypatch.setenv("SSH_CONNECTION", "test")
    monkeypatch.delenv("TMUX", raising=False)
    if tmux:
        monkeypatch.setenv("TMUX", "test")
    output = StringIO()
    text = "中文\x1b]0;not an escape\x07"
    result = asyncio.run(
        clipboard.write_clipboard(
            text, console=Console(file=output, force_terminal=True), cwd=tmp_path
        )
    )
    assert base64.b64encode(text.encode()).decode() in output.getvalue()
    assert "not an escape" not in output.getvalue()
    assert output.getvalue().startswith("\x1bPtmux;" if tmux else "\x1b]52;")
    assert "permission" in result


@pytest.mark.parametrize("error", [ValueError("failed"), asyncio.CancelledError()])
def test_clipboard_failure_or_cancel_does_not_claim_success(tmp_path, monkeypatch, error):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)

    async def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(clipboard, "run_owned", run)
    with pytest.raises(type(error)):
        asyncio.run(
            clipboard.write_clipboard("text", console=Console(file=StringIO()), cwd=tmp_path)
        )
