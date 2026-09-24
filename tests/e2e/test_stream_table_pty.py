"""Mutable table rows use the real active composer, not durable raw output."""

import os
import sys
import time
from io import StringIO

import pexpect
import pytest
from rich.text import Text

PROGRAM = r"""
import asyncio
import os
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelReasoningDelta, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, new_step_id

async def main():
    release = asyncio.Event()
    wrapped = os.environ.get("CORKI_TEST_TABLE_WRAPPED") == "1"
    opening, closing = ("```markdown\n", "```\n") if wrapped else ("", "")
    source = ("Before **bold** and `code`\n\n" + opening + "| Name | Value |\n"
              "| --- | --- |\n| alpha | one |\n| PARTIAL")
    class Model:
        calls = 0
        async def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                yield ModelReasoningDelta("REASONING_BEFORE_BODY")
                yield ModelTextDelta(source)
                print("TABLE_GATED", flush=True)
                await release.wait()
                text = source + " | two |\n" + closing
                yield ModelTextDelta(" | two |\n" + closing)
            else:
                previous = [item for item in request.items
                            if isinstance(item, AssistantMessageItem)]
                assert previous[-1].text == source + " | two |\n" + closing
                text = "followed up"
            yield ModelCompleted((AssistantMessageItem(text,
                request.items[-1].turn_id, new_step_id()),))
        async def aclose(self):
            pass
    class Application(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TABLE_TURN_FINISHED", flush=True)
    cwd = Path.cwd()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "sessions.db", home_path=cwd / "home")
    steer = runtime.steer
    async def gated_steer(message):
        await steer(message)
        release.set()
    runtime.steer = gated_steer
    ui = TerminalUI(settings, cwd / "input-history")
    app = Application(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.calls == 2
    assert ui._table_source is None
    recorded = [args[0] for method, args, kwargs in ui._transcript.calls
                if method.__name__ == "show_assistant_message"]
    assert source + " | two |\n" + closing in recorded
    for width in (40, 100):
        replay = ui._transcript.render(width)
        assert replay.count("alpha") == 1 and replay.count("PARTIAL") == 1
        assert "| --- | --- |" not in replay
    print("TABLE_STREAM_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("wrapped", [False, True])
def test_mutable_table_uses_active_composer(tmp_path, width, wrapped):
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
            "CORKI_TEST_TABLE_WRAPPED": "1" if wrapped else "0",
        },
    )
    captured = StringIO()
    child.logfile_read = captured
    try:
        child.expect("Ask Corki to do anything")
        child.send("start")
        time.sleep(0.15)
        child.send("\r")
        child.expect_exact("alpha")
        prefix = child.before
        # The first live table draw must follow its durable prefix. Do not
        # accept a later stdout flush or final transcript repair as evidence.
        assert "bold" in prefix and "**bold**" not in prefix
        assert "REASONING_BEFORE_BODY" not in prefix
        assert "`code" not in prefix
        assert "PARTIAL" not in child.before
        child.expect_exact("TABLE_GATED")
        child.sendcontrol("t")
        child.expect_exact("REASONING_BEFORE_BODY")
        child.sendcontrol("t")
        child.expect_exact("\x1b[?1049l")
        child.setwinsize(30, 100 if width == 40 else 40)
        for character in "continue":
            child.send(character)
            # A real terminal drains redraws while the user types. Leaving the
            # PTY unread can fill its output buffer during resize, block the
            # event loop, and turn separately sent keys into one paste burst.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    child.read_nonblocking(65536, timeout=0.05)
                except pexpect.TIMEOUT:
                    break
        time.sleep(0.15)
        child.send("\r")
        try:
            child.expect_exact("TABLE_TURN_FINISHED")
        except (pexpect.TIMEOUT, pexpect.EOF):
            rendered = Text.from_ansi(captured.getvalue()).plain
            pytest.fail(f"table did not settle: {rendered[-7000:]!r}")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("TABLE_STREAM_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
