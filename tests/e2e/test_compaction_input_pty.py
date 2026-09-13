"""The real composer stays available during ordinary-request compaction."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio
import sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError
from corki.protocol.items import AssistantMessageItem, new_step_id

async def main():
    cancel = sys.argv[1] == "cancel"
    release = asyncio.Event()
    requests = []
    class Model:
        async def stream(self, request):
            content = request.items[-1].content
            compact = "checkpoint compaction" in content
            requests.append("<compact>" if compact else content)
            if compact:
                print("COMPACT_WAITING", flush=True)
                await release.wait()
                if sys.argv[1] == "fail":
                    raise ModelError("summary unavailable")
            yield ModelCompleted((AssistantMessageItem("done",
                request.items[-1].turn_id, new_step_id()),))
        async def aclose(self):
            pass
    class UI(TerminalUI):
        queued = 0
        def show_notice(self, message):
            super().show_notice(message)
            if message == "Queued for the next turn.":
                self.queued += 1
                if self.queued == 2 and not cancel:
                    release.set()
        def restore_queued_inputs(self, messages):
            super().restore_queued_inputs(messages)
            assert cancel and requests == ["seed", "<compact>"]
            assert self._draft == "after one\nafter two"
            print("COMPACT_INPUT_RESTORED", flush=True)
    class Application(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            if message.endswith("after two"):
                print("FOLLOWUPS_FINISHED", flush=True)
    cwd = Path.cwd()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
        model_max_retries=0, realtime_enabled=sys.argv[2] == 'true')
    runtime = LangGraphRuntime.create(settings=settings, model=Model(),
        database_path=cwd / "sessions.db", home_path=cwd / "home")
    async for _ in runtime.stream("seed"):
        pass
    app = Application(settings, CorkiPaths.from_home(cwd / "home"), runtime,
        UI(settings, cwd / "input-history"))
    assert await app.run() == 0
    assert requests == ["seed", "<compact>"] + (
        ["after one\nafter two"] if cancel else ["after one", "after two"])
    print("COMPACTION_INPUT_VERIFIED", flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("outcome", ["complete", "cancel", "fail"])
@pytest.mark.parametrize("realtime", ["true", "false"])
def test_compaction_composer_queue_and_stop(tmp_path, width, outcome, realtime):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, outcome, realtime],
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
        child.send("/compact\r")
        child.expect_exact("COMPACT_WAITING")
        child.send("after one\r")
        child.expect_exact("Queued for the next turn.")
        child.send("after two\t")
        child.expect_exact("Queued for the next turn.")
        if outcome == "cancel":
            child.send("/stop\r")
            child.expect_exact("COMPACT_INPUT_RESTORED")
            child.expect_exact("after two")
            child.send("\r")
        if outcome == "fail":
            child.expect_exact("summary unavailable")
        child.expect_exact("FOLLOWUPS_FINISHED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("COMPACTION_INPUT_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
