"""Typed /plan commands through the real composer, application and Runtime."""

import os
import sys
import time

import pexpect
import pytest

BOOTSTRAP = r"""
import asyncio, sys
from pathlib import Path
from prompt_toolkit.formatted_text import to_formatted_text
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id

class Model:
    count = 0
    cycling = sys.argv[1] == "cycle"
    async def stream(self, request):
        self.count += 1
        assert self.count == 1
        assert request.reasoning_effort == ("low" if self.cycling else "medium")
        assert [i.content for i in request.items if isinstance(i, UserMessageItem)] == [
            "Review Foo\nKeepCase"
        ]
        assert any(isinstance(i, ContextItem) and "Plan Mode" in i.content
                   for i in request.items) == (not self.cycling)
        yield ModelCompleted((AssistantMessageItem(
            "Typed plan accepted.", request.items[-1].turn_id, new_step_id()
        ),))
    async def aclose(self): pass

async def main():
    cwd = Path.cwd()
    fail = sys.argv[1] == "fail"
    cycling = sys.argv[1].startswith("cycle")
    cycle_failed = sys.argv[1] == "cycle_fail"
    settings = CorkiSettings(working_directory=cwd, skills_enabled=False,
        plugins_enabled=False, realtime_enabled=False, reasoning_effort="low")
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, database_path=cwd / "session.db",
                                      model=model)
    if fail:
        async def fail_update(**kwargs):
            raise OSError("fixture settings write failed")
        runtime.update_thread_settings = fail_update
    elif cycle_failed:
        update = runtime.update_thread_settings
        async def fail_default(**kwargs):
            if kwargs["collaboration_mode"].mode == "default":
                raise OSError("fixture Default publication failed")
            return await update(**kwargs)
        runtime.update_thread_settings = fail_default

    class UI(TerminalUI):
        reads = 0
        updates = 0
        def set_collaboration_mode(self, mode):
            assert not fail
            expected = "default" if cycling and self.updates == 1 else "plan"
            assert runtime.thread_settings.collaboration_mode == mode == expected
            self.updates += 1
            super().set_collaboration_mode(mode)
        async def read_message(self):
            if not self._mode_cycle_enabled:
                # The busy composer stays mounted without enabling mode cycling.
                return await super().read_message()
            self.reads += 1
            if self.reads >= 2:
                toolbar = "".join(text for _, text in to_formatted_text(self._toolbar()))
                expected_plan = (self.reads == 2 or cycle_failed) if cycling else not fail
                assert ("Plan mode" in toolbar) == expected_plan, toolbar
                final_read = 4 if cycling else 3
                assert model.count == (int(not fail) if self.reads == final_read else 0)
                if cycling and self.reads in (2, 3):
                    assert self._draft == "Review Foo\nKeepCase"
                    assert not list(self._session.history.get_strings())
            print(f"READY_INPUT_{self.reads}", flush=True)
            return await super().read_message()

    ui = UI(settings, cwd / "input-history")
    app = CorkiApplication(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert ui.reads == (4 if cycling else 3)
    assert ui.updates == (0 if fail else 1 if cycle_failed else 2)
    assert model.count == (0 if fail else 1)
    if fail:
        assert list(app._pending_messages) == ["/plan Review Foo\nKeepCase", "/plan"]
        assert not app._queue_autosend
    else:
        assert not app._pending_messages
    print("RESULT_OK", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("mode", ["success", "fail", "cycle", "cycle_fail"])
def test_typed_plan_publication_and_inline_submission(tmp_path, width, mode):
    child = pexpect.spawn(
        sys.executable,
        ["-c", BOOTSTRAP, mode],
        cwd=str(tmp_path),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "0",
        },
        dimensions=(30, width),
        encoding="utf-8",
        timeout=20,
    )

    def expect_rendered(text):
        # Act as a CPR-capable terminal: prompt-toolkit needs the available
        # height before it can render the bottom toolbar. A bare PTY supplies
        # neither a terminal emulator nor cursor-position replies.
        while child.expect_exact([text, "\x1b[6n"]) == 1:
            child.send("\x1b[1;1R")

    try:
        expect_rendered("READY_INPUT_1")
        expect_rendered("Ask Corki to do anything")
        if mode.startswith("cycle"):
            child.send("\x1b[200~Review Foo\nKeepCase\x1b[201~\x1b[Z")
        else:
            child.send("/plan")
            time.sleep(0.15)
            child.send("\r")
        expect_rendered("READY_INPUT_2")
        if mode.startswith("cycle"):
            child.send("\x1b[Z")
        else:
            child.send("\x1b[200~/plan Review Foo\nKeepCase\x1b[201~\r")
        if mode == "cycle_fail":
            expect_rendered("Default mode update failed")
        expect_rendered("READY_INPUT_3")
        if mode.startswith("cycle"):
            child.send("\r")
            expect_rendered("READY_INPUT_4")
        child.sendcontrol("d")
        expect_rendered("RESULT_OK")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
