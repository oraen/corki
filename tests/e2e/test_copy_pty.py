"""Exercise /copy through the actual runtime and PTY without touching the user's clipboard."""

import os
import sys
import time

import pexpect
import pytest

PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli import clipboard
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id

async def main():
    cwd = Path.cwd()
    text = "Answer\n\n```python\nprint('hello')\n```\n\n> quote me\n"
    copied = []
    async def copy(value, **kwargs):
        copied.append(value)
        return "COPY_CAPTURED"
    clipboard.write_clipboard = copy
    class Model:
        calls = 0
        async def stream(self, request):
            self.calls += 1
            item = AssistantMessageItem(text, request.items[-1].turn_id, new_step_id())
            yield ModelCompleted((item,))
        async def aclose(self): pass
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "state.db", home_path=cwd / "home")
    ui = TerminalUI(settings, cwd / "history")
    class Application(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    app = Application(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.calls == 1
    assert copied == ["print('hello')\n", text]
    assert not ui._transcript.modal_depth and ui._model_menu is None
    assert app._input._reader is None and not app._input._modals
    assert not list(ui._form_session.history.get_strings())
    print("COPY_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_copy_picker_cancel_select_and_followup(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=20,
        dimensions=(30, width),
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("/copy")
        time.sleep(0.15)
        child.send("\r")
        child.expect("No agent response to copy")
        child.expect("Ask Corki to do anything")
        child.send("hello")
        time.sleep(0.15)
        child.send("\r")
        child.expect("quote me")
        child.expect("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        child.send("/copy")
        time.sleep(0.15)
        child.send("\r")
        child.expect("Copy from response")
        child.send("\x1b")
        child.expect("Ask Corki to do anything")
        child.send("/copy")
        time.sleep(0.15)
        child.send("\r")
        child.expect("Copy from response")
        child.send("\x1b[B\r")
        child.expect("COPY_CAPTURED")
        child.expect("Ask Corki to do anything")
        child.send("/copy")
        time.sleep(0.15)
        child.send("\r")
        child.expect("Copy from response")
        child.send("\r")
        child.expect("COPY_CAPTURED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect("COPY_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
