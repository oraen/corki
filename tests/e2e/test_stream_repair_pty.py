"""A completed model item repairs obsolete streamed content on the actual terminal."""

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
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, new_step_id
from rich.console import Console

class Model:
    closed = False
    async def stream(self, request):
        yield ModelTextDelta('obsolete fragment')
        print('FIXTURE_WAIT', flush=True)
        await asyncio.to_thread(input)
        yield ModelCompleted((AssistantMessageItem(
            'Authoritative final text', request.items[-1].turn_id, new_step_id()
        ),))
    async def aclose(self):
        self.closed = True

class UI(TerminalUI):
    def append_assistant_delta(self, delta):
        super().append_assistant_delta(delta)
        print('DELTA_RENDERED', flush=True)

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(working_directory=cwd, skills_enabled=False, plugins_enabled=False)
    ui = UI(settings, cwd / 'history', console=Console(color_system=None))
    model = Model()
    runtime = LangGraphRuntime.create(
        settings=settings, database_path=cwd / 'sessions.db', model=model
    )
    app = CorkiApplication(settings, CorkiPaths.from_home(cwd / 'home'), runtime, ui)
    try:
        await app._consume_events(runtime.stream('Respond'))
        source = ui._transcript.render(100)
        assert 'obsolete fragment' not in source
        assert source.count('Authoritative final text') == 1
    finally:
        await runtime.aclose()
    assert model.closed
    print('FIXTURE_CLOSED', flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_completed_long_code_preserves_continuation_indent(tmp_path, width):
    code = "    " + " ".join(f"word{index}" for index in range(30))
    source = f"Intro\n\n```text\n{code}\n```"
    program = PROGRAM.replace(
        "        assert source.count('Authoritative final text') == 1",
        "        assert source.count('word29') == 1",
    ).replace("'Authoritative final text'", repr(source))
    child = pexpect.spawn(
        sys.executable,
        ["-c", program],
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
        child.expect_exact("DELTA_RENDERED")
        assert "obsolete fragment" not in child.before
        child.sendline("")
        child.expect_exact("\x1b[3J\x1b[2J\x1b[H")
        child.expect_exact("FIXTURE_CLOSED")
        lines = [line for line in child.before.splitlines() if "word" in line]
        assert len(lines) > 1
        assert all(line.startswith("      ") and len(line) <= width for line in lines)
        assert " ".join(line.strip() for line in lines) == code.strip()
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("nested", [False, True])
def test_identical_markdown_completion_repairs_raw_stream(tmp_path, width, nested):
    raw = r"1. outer\n   - inner" if nested else "**Bold** and `code`"
    rendered = "1. outer" if nested else "Bold and code"
    program = (
        PROGRAM.replace("        assert 'obsolete fragment' not in source\n", "")
        .replace("obsolete fragment", raw)
        .replace("Authoritative final text", raw)
        .replace(f"source.count('{raw}')", f"source.count('{rendered}')")
    )
    child = pexpect.spawn(
        sys.executable,
        ["-c", program],
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
        child.expect_exact("DELTA_RENDERED")
        if nested:
            # Delta admission now queues rendered lines; the model stays held
            # while the display's independent tick commits the stable prefix.
            before = child.before
            if "1. outer" not in before:
                child.expect_exact("1. outer")
                before += child.before
            assert "inner" not in before
        else:
            assert raw not in child.before
        child.sendline("")
        child.expect_exact("\x1b[3J\x1b[2J\x1b[H")
        child.expect_exact("FIXTURE_CLOSED")
        assert child.before.count(rendered) == 1
        assert "**Bold**" not in child.before and "`code`" not in child.before
        if nested:
            assert child.before.count("      - inner") == 1
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("resize", [False, True])
@pytest.mark.parametrize("same_text", [False, True])
def test_completed_item_repairs_terminal(tmp_path, width, resize, same_text):
    program = PROGRAM.replace(
        "    try:\n        await app._consume_events",
        "    watcher = asyncio.create_task(ui.watch_resize())\n"
        "    try:\n        await app._consume_events",
    ).replace(
        "    finally:\n        await runtime.aclose()",
        "    finally:\n        watcher.cancel()\n"
        "        await asyncio.gather(watcher, return_exceptions=True)\n"
        "        await runtime.aclose()",
    )
    final = "Authoritative final text"
    if same_text:
        final = "obsolete fragment"
        program = program.replace("        assert 'obsolete fragment' not in source\n", "")
        program = program.replace("Authoritative final text", final)
    child = pexpect.spawn(
        sys.executable,
        ["-c", program],
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
        child.expect_exact("DELTA_RENDERED")
        assert "obsolete fragment" not in child.before
        if resize:
            child.setwinsize(35, 100 if width == 40 else 40)
            child.expect_exact("\x1b[3J\x1b[2J\x1b[H")
        # Model producer and event consumer run independently; the marker can precede rendering.
        child.sendline("")
        if resize or not same_text:
            child.expect_exact("\x1b[3J\x1b[2J\x1b[H")
        child.expect_exact("FIXTURE_CLOSED")
        repaired = child.before
        if not same_text:
            assert "obsolete fragment" not in repaired
        if resize or not same_text:
            assert repaired.count(final) == 1
        else:
            assert "\x1b[3J" not in repaired
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
