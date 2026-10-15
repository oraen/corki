"""Physical SIGINT while a real Runtime mode commit is in flight."""

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

async def main():
    cwd = Path.cwd()
    failed = sys.argv[1] == "fail"
    expected = "default" if failed else "plan"
    parent = asyncio.current_task()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
                            realtime_enabled=False, reasoning_effort="low")
    class Model:
        closed = False
        async def stream(self, request):
            raise AssertionError("cancelled command must not sample")
            yield
        async def aclose(self): self.closed = True
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, database_path=cwd / "session.db",
                                     home_path=cwd / "home", model=model)
    await runtime._ensure_ready()
    save = runtime._repository.save_thread_model_settings
    async def held(*args):
        print("WRITE_HELD", flush=True)
        while not parent.cancelling():
            await asyncio.sleep(0.01)
        assert runtime.thread_settings.collaboration_mode == "default"
        print("SIGNAL_PENDING_COMMIT", flush=True)
        while not (cwd / "release-commit").exists():
            await asyncio.sleep(0.01)
        if failed:
            raise OSError("fixture commit rejected")
        await save(*args)
    runtime._repository.save_thread_model_settings = held
    class UI(TerminalUI):
        reads = 0
        modes = []
        notices = []
        def set_collaboration_mode(self, mode):
            assert runtime.thread_settings.collaboration_mode == mode == expected
            self.modes.append(mode)
            super().set_collaboration_mode(mode)
        def show_notice(self, text):
            self.notices.append(text)
            super().show_notice(text)
        async def read_message(self):
            self.reads += 1
            if self.reads == 2:
                assert self.modes == [expected]
                assert list(app._pending_messages) == ["/plan Review Foo\nKeepCase"]
                assert not app._queue_autosend
                assert "Turn interrupted." in self.notices
                assert "Plan mode enabled." not in self.notices
                print("CANCEL_STATE_VERIFIED", flush=True)
            return await super().read_message()
    ui = UI(settings, cwd / "history")
    app = CorkiApplication(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.closed and ui.reads == 2
    assert runtime.thread_settings.collaboration_mode == expected
    print("CANCEL_PTY_OK", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("commit", ["success", "fail"])
def test_ctrl_c_during_plan_commit_preserves_state_and_input(tmp_path, width, commit):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, commit],
        cwd=tmp_path,
        encoding="utf-8",
        dimensions=(30, width),
        timeout=15,
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "0",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )

    def expect(text):
        while child.expect_exact([text, "\x1b[6n"]) == 1:
            child.send("\x1b[1;1R")

    try:
        expect("Ask Corki to do anything")
        child.send("\x1b[200~/plan Review Foo\nKeepCase\x1b[201~\r")
        expect("WRITE_HELD")
        child.sendcontrol("c")
        expect("SIGNAL_PENDING_COMMIT")
        (tmp_path / "release-commit").write_text("release")
        expect("CANCEL_STATE_VERIFIED")
        expect("Ask Corki to do anything")
        child.sendcontrol("d")
        expect("CANCEL_PTY_OK")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
