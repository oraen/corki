"""Cancel a real streaming turn without losing its received, unclosed tail."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, new_step_id

async def main():
    cwd = Path.cwd()
    tail = "末尾内容🙂TAIL_RECEIVED"
    text = "已经收到的正文\n\n" + (
        "```text\n    " if sys.argv[1] == "code" else "") + tail
    class Model:
        calls = 0
        closed = False
        async def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                yield ModelTextDelta(text)
                await asyncio.Future()
            yield ModelCompleted((AssistantMessageItem(
                "Followup complete.", request.items[-1].turn_id, new_step_id()),))
        async def aclose(self): self.closed = True
    class UI(TerminalUI):
        def append_assistant_delta(self, delta):
            super().append_assistant_delta(delta)
            print("DELTA_READY", flush=True)
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            try:
                await super()._consume_turn(message)
            finally:
                print("TURN_SETTLED", flush=True)
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "state.db", home_path=cwd / "home")
    ui = UI(settings, cwd / "input-history")
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.closed and model.calls == 2
    for width in (40, 100):
        replay = ui._transcript.render(width)
        assert replay.count(tail) == 1
        assert replay.count("Turn interrupted.") == 1
    assert not ui._assistant_pending and ui._table_source is None
    print("TAIL_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("kind", ["plain", "code"])
def test_cancel_retains_unclosed_unicode_tail(tmp_path, width, kind):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, kind],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(30, width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("start\r")
        child.expect("DELTA_READY")
        child.sendcontrol("c")
        child.expect("TAIL_RECEIVED")
        child.expect("Turn interrupted")
        child.expect("Ask Corki to do anything")
        child.send("followup\r")
        child.expect("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect("TAIL_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
