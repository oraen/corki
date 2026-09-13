"""A gated model's plan table is visible only as a live, cancellable preview."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelTextDelta
from corki.protocol.items import AssistantMessageItem

async def main():
    class Model:
        calls = 0
        closed = False
        stream_closed = False
        async def stream(self, request):
            self.calls += 1
            try:
                yield ModelTextDelta(
                    "<proposed_plan>\n| Name | Value |\n| --- | --- |\n"
                    "| alpha | beta |\n| PARTIAL")
                while not ui._history_view.active:
                    await asyncio.sleep(0.01)
                yield ModelTextDelta(" | ΩΨЖ |\n")
                await asyncio.Event().wait()
                raise AssertionError("model must not complete")
            finally:
                self.stream_closed = True
        async def aclose(self):
            self.closed = True
    class Application(CorkiApplication):
        async def _consume_turn(self, message):
            try:
                await super()._consume_turn(message)
            finally:
                assert model.stream_closed
                assert ui._plan_stream is None
                assert ui._stream_tail_fragments() == []
                assert "alpha" not in "".join(
                    part[1] for part in ui._history_view.text())
                for width in (40, 100):
                    replay = ui._transcript.render(width)
                    assert all(word not in replay for word in ("alpha", "PARTIAL", "ΩΨЖ"))
                history = await runtime._repository.load_items(runtime.thread_id)
                assert not any(isinstance(i, AssistantMessageItem) for i in history)
                print("PLAN_PREVIEW_CLEARED", flush=True)
    cwd = Path.cwd()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
                            collaboration_mode="plan")
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "s.db", home_path=cwd / "home")
    ui = TerminalUI(settings, cwd / "input-history")
    app = Application(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.calls == 1 and model.closed
    print("PLAN_PREVIEW_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("keyboard", [False, True])
@pytest.mark.parametrize("browse_history", [False, True])
def test_plan_tail_visible_before_completion_and_removed_on_cancel(
    tmp_path, width, keyboard, browse_history
):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(30, width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("start\r")
        child.expect_exact("alpha")
        assert "Proposed Plan" in child.before
        assert "PARTIAL" not in child.before
        child.expect_exact("beta")
        if browse_history:
            child.send("retained draft\x14")
            child.expect_exact("\x1b[?1049h")
            # Match after entering the alternate screen, not the inline preview.
            child.expect_exact("alpha")
            assert "PARTIAL" not in child.before
            child.expect_exact("beta")
            # Use characters absent from the previous frame: the renderer can
            # otherwise reuse old cells by cursor movement within an ASCII word.
            child.expect_exact("ΩΨЖ")
            child.send("q")
            child.expect_exact("\x1b[?1049l")
            child.expect_exact("retained draft")
            child.sendcontrol("u")
        child.sendcontrol("c") if keyboard else child.send("/stop\r")
        child.expect_exact("PLAN_PREVIEW_CLEARED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("PLAN_PREVIEW_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
