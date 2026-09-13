"""A cold terminal shows a persisted failed Turn without resampling its partial answer."""

import os
import sys

import pexpect
import pytest

PROGRAM = """
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelError, ModelItemCompleted
from corki.protocol.events import TurnFailed
from corki.protocol.items import AssistantMessageItem, new_step_id
from rich.console import Console

class Model:
    calls = 0
    closed = 0
    async def stream(self, request):
        self.calls += 1
        assert self.calls == 1
        yield ModelItemCompleted(AssistantMessageItem(
            'Saved partial answer', request.items[-1].turn_id, new_step_id()
        ))
        raise ModelError('Saved failure marker')
    async def aclose(self):
        self.closed += 1

class UI(TerminalUI):
    async def read_message(self):
        raise EOFError

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(working_directory=cwd, skills_enabled=False, plugins_enabled=False)
    model = Model()
    warm = LangGraphRuntime.create(
        settings=settings, database_path=cwd / 'sessions.db', model=model, home_path=cwd
    )
    events = [event async for event in warm.stream('Saved request')]
    assert isinstance(events[-1], TurnFailed)
    thread = warm.thread_id
    await warm.aclose()
    cold = LangGraphRuntime.create(
        settings=settings, database_path=cwd / 'sessions.db', model=model,
        thread_id=thread, home_path=cwd
    )
    ui = UI(settings, cwd / 'history', console=Console(color_system=None))
    assert await CorkiApplication(settings, CorkiPaths.from_home(cwd), cold, ui).run() == 0
    for width in (40, 100):
        rendered = ui._transcript.render(width)
        assert rendered.count('Saved failure marker') == 1
        assert rendered.index('Saved partial answer') < rendered.index('Saved failure marker')
    assert model.calls == 1 and model.closed == 2
    print('COLD_HISTORY_CLOSED', flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_cold_failed_turn_terminal_replay(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=10,
        dimensions=(30, width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
    )
    try:
        child.expect_exact("Saved partial answer")
        child.expect_exact("Saved failure marker")
        child.expect_exact("COLD_HISTORY_CLOSED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
