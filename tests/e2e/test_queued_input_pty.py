"""Real Tab submissions queue follow-ups while Enter steers the owning Runtime."""

import os
import sys
from pathlib import Path

import pexpect
import pytest

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id

async def main():
    class Model:
        requests = []
        models = []
        closed = False
        release = asyncio.Event()
        async def stream(self, request):
            self.models.append(request.model)
            user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
            self.requests.append((user.content, user.turn_id))
            if len(self.requests) == 1:
                yield ModelTextDelta("Working\n")
                print("MODEL_WAITING", flush=True)
                await self.release.wait()
            if user.content == "queued two":
                print("ALL_REQUESTS_SAMPLED", flush=True)
            yield ModelCompleted((AssistantMessageItem("Done " + user.content,
                user.turn_id, new_step_id()),))
        async def aclose(self):
            self.closed = True
    cwd = Path.cwd()
    settings = CorkiSettings(working_directory=cwd, skills_enabled=False,
                            plugins_enabled=False,
                            mcp_servers=(MCPServerSettings("numbers", "stdio",
                                command=sys.executable, args=(sys.argv[1],)),))
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, database_path=cwd / "sessions.db",
                                     model=model, home_path=cwd / "home")
    original_steer = runtime.steer
    original_update = runtime.update_thread_settings
    changes = []
    async def update(**kwargs):
        changes.append(kwargs)
        return await original_update(**kwargs)
    runtime.update_thread_settings = update
    async def steer(message):
        assert message == "steer now"
        assert len(model.requests) == 1
        await original_steer(message)
        model.release.set()
    runtime.steer = steer
    ui = TerminalUI(settings, cwd / "history")
    class Application(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            if message == "queued two":
                print("ALL_TURNS_FINISHED", flush=True)
    app = Application(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.closed
    assert changes == [{"model": "Local/Other"}]
    assert model.models == [settings.model, settings.model, "Local/Other", "Local/Other"]
    assert [text for text, _ in model.requests] == [
        "initial", "steer now", "queued one", "queued two"]
    turns = [turn for _, turn in model.requests]
    assert turns[0] == turns[1] and len(set(turns)) == 3
    history = (cwd / "history").read_text()
    for text in ("initial", "steer now", "queued one", "queued two"):
        assert history.count("+" + text + "\n") == 1
    assert "QueuedInput" not in history
    assert "+Local/Other" not in history and "Discard/Model" not in history
    print("QUEUE_VERIFIED", flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("edit", [False, True])
def test_tab_queue_and_enter_steering_use_distinct_turns(tmp_path, width, edit):
    program = PROGRAM
    if edit:
        program = program.replace('"queued two"', '"queued two edited"')
        program = program.replace(
            '    assert "QueuedInput" not in history',
            '    assert history.count("+queued two\\n") == 1\n'
            '    assert "QueuedInput" not in history',
        )
    child = pexpect.spawn(
        sys.executable,
        ["-c", program, str(Path(__file__).resolve().parents[1] / "fixtures/mcp_number_server.py")],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
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
        child.expect("Ask Corki to do anything")
        child.send("initial\r")
        child.expect_exact("MODEL_WAITING")
        child.send("/model\r")
        child.expect_exact("Select model")
        child.send("Discard/Model")
        child.send("\x1b")
        child.expect("Ask Corki to do anything")
        child.send("/model\r")
        child.expect_exact("Select model")
        child.send("Invalid Model\r")
        child.expect_exact("Model names cannot contain")
        child.send("\x15Local/Other\r")
        child.expect_exact("Model: Local/Other.")
        child.expect_exact("turns.")
        child.expect("Ask Corki to do anything")
        for command, feedback in (
            ("/mcp", "mcp__numbers::read"),
            ("/status", "Corki home:"),
            ("/missing", "Unknown command: /missing"),
            ("/clear", "unavailable while"),
        ):
            child.send(command + "\r")
            child.expect_exact(feedback)
            child.expect("Ask Corki to do anything")
        child.send("queued one\t")
        child.expect_exact("Queued for the next turn.")
        child.expect_exact("Queued follow-up inputs")
        child.expect_exact("↳ queued one")
        child.send("queued two\t")
        child.expect_exact("Queued for the next turn.")
        child.expect_exact("Queued follow-up inputs")
        child.expect_exact("↳ queued one")
        child.expect_exact("↳ queued two")
        if edit:
            child.expect_exact("edit last queued message")
            child.send("\x1b\x1b[A")
            child.send(" edited\t")
            child.expect_exact("Queued for the next turn.")
            child.expect_exact("↳ queued two edited")
        child.send("steer now\r")
        child.expect_exact("ALL_REQUESTS_SAMPLED")
        child.expect_exact("ALL_TURNS_FINISHED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("QUEUE_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
