"""A failed model connection returns to the composer for a later successful turn."""

import os
import sys
from io import StringIO

import pexpect
import pyte
import pytest

PROGRAM = r"""
import asyncio, sys
import httpx
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.models.http_errors import http_error
from corki.protocol.items import AssistantMessageItem, new_step_id
from rich.text import Text

async def main():
    cwd = Path.cwd()
    mode = sys.argv[1]
    retry = mode in {"retry", "draft_retry"}
    auth = mode == "auth"
    failure = (
        "model request failed (401): Incorrect API key"
        if auth else "fixture connection unavailable"
    )
    draft_retry = mode == "draft_retry"
    release_retry = asyncio.Event()
    if retry:
        import corki.core.graph as graph
        async def skip_fixture_wait(delay, realtime):
            if draft_retry:
                await release_retry.wait()
            else:
                await asyncio.sleep(0)
        graph.wait_retry = skip_fixture_wait
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
        model_max_retries=int(retry), model_unbounded_connection_retries=False)
    class Model:
        calls = 0
        closed = False
        async def stream(self, request):
            self.calls += 1
            if self.calls <= (2 if retry else 1):
                if auth:
                    raise http_error(httpx.Response(401),
                        '{"error": {"message": "Incorrect API key"}}')
                raise ModelError("fixture connection unavailable", kind=ModelErrorKind.CONNECTION,
                    retryable=retry)
            yield ModelCompleted((AssistantMessageItem(
                "FOLLOWUP_ANSWER", request.items[-1].turn_id, new_step_id()),))
        async def aclose(self):
            self.closed = True
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    class UI(TerminalUI):
        def __init__(self, *args):
            super().__init__(*args)
            if draft_retry:
                def edited(buffer):
                    if buffer.text == "draft":
                        release_retry.set()
                self._session.default_buffer.on_text_changed += edited
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "state.db", home_path=cwd / "home")
    ui = UI(settings, cwd / "input-history")
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.calls == (3 if retry else 2) and model.closed
    replay = ui._transcript.render(100)
    replay_text = Text.from_ansi(replay).plain
    assert replay_text.count("• " + failure) == 1
    assert replay_text.count("Reason: " + failure) == int(retry)
    assert replay_text.count("FOLLOWUP_ANSWER") == 1
    assert list(ui._session.history.get_strings()) == [
        "first", "draft" if draft_retry else "second"]
    print("CONNECTION_RECOVERY_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("mode", ["immediate", "retry", "draft_retry", "auth"])
def test_failed_connection_keeps_cli_usable_for_next_turn(tmp_path, width, mode):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, mode],
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
    output = StringIO()
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        child.send("first\r")
        if mode in {"retry", "draft_retry"}:
            child.expect("Reconnecting")
        if mode == "draft_retry":
            child.send("draft")
        failure = (
            "model request failed (401): Incorrect API key"
            if mode == "auth"
            else "fixture connection unavailable"
        )
        try:
            child.expect_exact(
                "model request failed (401): Incorrect" if mode == "auth" else failure
            )
        except pexpect.TIMEOUT:
            pytest.fail(f"model error was not visible: {output.getvalue()[-3000:]!r}")
        child.expect("TURN_SETTLED")
        if mode == "draft_retry":
            child.expect("draft")
            waiting_screen = pyte.Screen(width, 30)
            pyte.Stream(waiting_screen).feed(output.getvalue())
            drafts = [row.strip() for row in waiting_screen.display if "› draft" in row]
            assert drafts and drafts[-1] == "› draft"
            child.send("\r")
        else:
            child.expect("Ask Corki to do anything")
            child.send("second\r")
        child.expect("FOLLOWUP_ANSWER")
        child.expect("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        screen = pyte.Screen(width, 30)
        pyte.Stream(screen).feed(output.getvalue())
        visible = "\n".join(screen.display)
        if mode == "auth":
            assert " ".join(visible.split()).count(failure) == 1, visible
        else:
            assert visible.count("• " + failure) == 1, visible
        assert visible.count("Reason: " + failure) == int(mode in {"retry", "draft_retry"})
        if mode == "auth":
            assert "Reconnecting" not in visible
        assert visible.count("FOLLOWUP_ANSWER") == 1, visible
        assert "Turn interrupted" not in visible
        child.sendcontrol("d")
        child.expect("CONNECTION_RECOVERY_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
