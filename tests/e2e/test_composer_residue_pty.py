"""Cancelled realtime readers must not commit placeholder/draft lines to scrollback."""

import os
import re
import sys
import time
from io import StringIO

import pexpect
import pyte
import pytest

PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id

async def main():
    cwd = Path.cwd()
    class Model:
        calls = 0
        async def stream(self, request):
            self.calls += 1
            await asyncio.sleep(0.3)
            yield ModelCompleted((AssistantMessageItem(
                "ANSWER_" + str(self.calls), request.items[-1].turn_id, new_step_id()),))
        async def aclose(self): pass
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "state.db", home_path=cwd / "home")
    ui = TerminalUI(settings, cwd / "history")
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert list(ui._session.history.get_strings()) == ["hello0", "hello1", "hello2"]
    assert not ui._working_status.active and ui._working_status.timer is None
    print("CLEAN_EXIT", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_only_one_placeholder_after_each_completed_turn(tmp_path, width):
    output = StringIO()
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        dimensions=(30, width),
        timeout=15,
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        for index in range(3):
            child.send(f"hello{index}")
            time.sleep(0.15)
            child.send("\r")
            child.expect(f"ANSWER_{index + 1}")
            child.expect("TURN_SETTLED")
            child.expect("Ask Corki to do anything")
            screen = pyte.HistoryScreen(width, 30, history=1000)
            # pyte does not implement Kitty's keyboard-mode CSI >u / <u and
            # otherwise paints a spurious 'u'. These sequences change input
            # reporting only; remove them from the virtual-screen fixture.
            display = re.sub(r"\x1b\[(?:>1u|<u|>4;[02]m)", "", output.getvalue())
            pyte.Stream(screen).feed(display)
            rows = [
                "".join(line[column].data for column in range(width)) for line in screen.history.top
            ] + screen.display
            assert sum("Ask Corki to do anything" in row for row in rows) == 1, "\n".join(rows)
            assert sum(f"› hello{index}" in row for row in rows) == 1
            work_rows = [row for row in rows if "Worked for" in row]
            assert not work_rows, repr(work_rows)
        child.sendcontrol("d")
        child.expect("CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
