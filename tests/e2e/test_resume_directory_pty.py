"""Production resume entry point, real local tool, and no model service."""

import os
import sys
import time
from contextlib import suppress

import pexpect
import pyte
import pytest

PROGRAM = r"""
import asyncio, importlib, sys
from pathlib import Path
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem, ToolCallItem, ToolResultItem
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository

entry = importlib.import_module('corki.cli.main')
current = Path.cwd()
saved = current / '中文 saved'
paths = CorkiPaths.discover()
paths.ensure_exists()
thread, turn = new_thread_id(), new_turn_id()
async def seed():
    repo = SQLiteSessionRepository(paths.sessions_dir / 'corki.db')
    try:
        await repo.create_thread(thread, saved)
        await repo.save_turn(TurnRecord(turn, thread, TurnStatus.COMPLETED, 'CWD_TARGET'))
        await repo.append_items(thread, (UserMessageItem('CWD_TARGET', turn),))
    finally:
        await repo.close()
asyncio.run(seed())

calls = []
expected = 'SAVED_DIRECTORY' if sys.argv[1] in ('saved', 'remember-saved') else 'CURRENT_DIRECTORY'
class Model:
    async def stream(self, request):
        calls.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(calls) == 1:
            call = ToolCall('cwd-check-' + str(turn), 'exec_command',
                            {'cmd': 'cat cwd-marker', 'login': False})
            yield ModelCompleted((ToolCallItem(call, turn, step),))
        else:
            results = [i for i in request.items if isinstance(i, ToolResultItem)]
            assert results[-1].exit_code == 0
            assert expected in results[-1].content, results[-1].content
            yield ModelCompleted((AssistantMessageItem('DIRECTORY_VERIFIED', turn, step),))
    async def aclose(self): pass

original_create = LangGraphRuntime.create
def create(**kwargs):
    kwargs['model'] = Model()
    return original_create(**kwargs)
entry.LangGraphRuntime.create = create
# Do not load real credentials or user provider configuration for this test.
entry.CorkiSettings.for_directory = lambda directory, **kw: CorkiSettings(
    directory, skills_enabled=False, plugins_enabled=False, execution_permissions=None)
entry.load_mcp_requirements = lambda: None
original_app = entry.CorkiApplication
class App(original_app):
    async def _consume_turn(self, message):
        await super()._consume_turn(message)
        print('TURN_SETTLED', flush=True)
entry.CorkiApplication = App
arguments = {
    'picker': ['resume'], 'id': ['resume', str(thread)], 'legacy': ['--resume', str(thread)]
}[sys.argv[2]]
assert entry.main(arguments) == 0
assert len(calls) == (0 if sys.argv[1] == 'cancel' else 2)
assert Path.cwd() == current
if sys.argv[1].startswith('remember-'):
    calls.clear()
    print('REPEAT_RESUME', flush=True)
    assert entry.main(['resume', str(thread)]) == 0
    assert len(calls) == 2
    assert Path.cwd() == current
print('RESUME_CLEAN_EXIT', flush=True)
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize(
    "choice", ["saved", "current", "cancel", "remember-saved", "remember-current"]
)
@pytest.mark.parametrize("entrypoint", ["picker", "id", "legacy"])
def test_cross_directory_resume_controls_actual_tool_cwd(tmp_path, width, choice, entrypoint):
    saved = tmp_path / "中文 saved"
    saved.mkdir()
    (saved / "cwd-marker").write_text("SAVED_DIRECTORY")
    (tmp_path / "cwd-marker").write_text("CURRENT_DIRECTORY")
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, choice, entrypoint],
        cwd=tmp_path,
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
        encoding="utf-8",
        dimensions=(26, width),
        timeout=15,
    )
    try:
        if entrypoint == "picker":
            screen = pyte.Screen(width, 26)
            stream = pyte.Stream(screen)
            deadline = time.monotonic() + 10
            while "No matching sessions." not in "\n".join(screen.display):
                assert time.monotonic() < deadline, screen.display
                with suppress(pexpect.TIMEOUT):
                    stream.feed(child.read_nonblocking(8192, timeout=0.2))
            child.send("\x1b[C")
            child.expect_exact("CWD_TARGET")
            child.send("\r")
        child.expect_exact("Session directory")
        child.setwinsize(26, 100 if width == 40 else 40)
        if choice == "cancel":
            child.sendcontrol("c")
        else:
            child.send(
                {"saved": "\x1b", "current": "2", "remember-saved": "3", "remember-current": "4"}[
                    choice
                ]
            )
            child.expect_exact("Ask Corki to do anything")
            child.send("check working directory")
            time.sleep(0.15)
            child.send("\r")
            child.expect_exact("DIRECTORY_VERIFIED")
            child.expect_exact("TURN_SETTLED")
            child.expect_exact("Ask Corki to do anything")
            child.sendcontrol("d")
            if choice.startswith("remember-"):
                child.expect_exact("REPEAT_RESUME")
                child.expect_exact("Ask Corki to do anything")
                assert "Choose a working directory" not in child.before
                child.send("check remembered working directory")
                time.sleep(0.15)
                child.send("\r")
                child.expect_exact("DIRECTORY_VERIFIED")
                child.expect_exact("TURN_SETTLED")
                child.expect_exact("Ask Corki to do anything")
                child.sendcontrol("d")
        child.expect_exact("RESUME_CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
