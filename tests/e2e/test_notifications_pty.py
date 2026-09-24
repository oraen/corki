import os
import re
import sys
from io import StringIO

import pexpect
import pytest

APPROVAL_PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from types import SimpleNamespace
from corki.cli.application import CorkiApplication, _DisplayStreams
from corki.cli.input_owner import InputOwner
from corki.cli.terminal import TerminalUI
from corki.cli.terminal_responses import frame_terminal_responses
from corki.config import CorkiSettings
from corki.mcp.elicitation import ElicitationRequest
from corki.protocol.events import TurnCompleted

async def main():
    ui = TerminalUI(CorkiSettings(Path.cwd(), notification_method='osc9'), Path('history'))
    owner = InputOwner(ui)
    decisions = []
    app = CorkiApplication.__new__(CorkiApplication)
    app._ui, app._input = ui, owner
    app._runtime = SimpleNamespace(
        respond_execution_approval=lambda *a, **kw: decisions.append((a, kw)))
    request = ElicitationRequest('PRIVATE_SERVER', 'PRIVATE_REQUEST', {
        'message': 'Review fixture operation',
        '_meta': {'tool_params': {'command': 'echo PRIVATE_COMMAND'}},
        'requestedSchema': {'type': 'object', 'properties': {}}
    }, 'shell_approval')
    with ui.input_mode():
        composer = asyncio.create_task(owner.read_message())
        try:
            async with asyncio.timeout(10):
                while (ui._session.default_buffer.text != 'saved draft'
                       or ui._notifications.focused != (sys.argv[1] == 'focused')):
                    await asyncio.sleep(.01)
                ui.set_turn_active(True)
                approval = asyncio.create_task(app._handle_elicitation(request))
                try:
                    while not ui._notifications.seen:
                        await asyncio.sleep(.01)
                    # Duplicate delivery of the same notification is not a second approval.
                    app._notify('approval-requested', ('PRIVATE_SERVER', 'PRIVATE_REQUEST'))
                    await approval
                finally:
                    approval.cancel()
                    await asyncio.gather(approval, return_exceptions=True)
                assert len(decisions) == 1
                assert decisions[0][0] == ('PRIVATE_REQUEST', sys.argv[2])
                assert owner._modals == 0
                ui.set_turn_active(False)
                print('APPROVAL_SETTLED', flush=True)
                assert await composer == 'saved draft'
                app._replayed_answer_turns = set()
                async def completed():
                    yield TurnCompleted('local', 'PRIVATE_TURN', '')
                    yield TurnCompleted('local', 'PRIVATE_TURN', '')
                await app._render_events_owned(completed(), _DisplayStreams())
                await asyncio.sleep(0)
        finally:
            composer.cancel()
            await asyncio.gather(composer, return_exceptions=True)
    assert owner._reader is None
    assert ui._notifications.handle is None and ui._notifications.pending is None
    assert not ui._notifications.seen
    for session in (ui._session, ui._form_session):
        parser = frame_terminal_responses(session.app.input)
        assert ui._notifications.set_focus not in parser.focus_listeners
        assert not session.app._background_tasks
    print('APPROVAL_NOTIFY_CLEAN_EXIT', flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("focus", ["focused", "unfocused"])
@pytest.mark.parametrize("decision", ["accept", "cancel"])
def test_application_approval_notification_preserves_draft_and_cleans_up(
    tmp_path, width, focus, decision
):
    child = pexpect.spawn(
        sys.executable,
        ["-c", APPROVAL_PROGRAM, focus, decision],
        cwd=tmp_path,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1", "TMUX": ""},
        encoding="utf-8",
        dimensions=(24, width),
        timeout=15,
    )
    output = StringIO()
    child.logfile_read = output
    try:
        child.expect_exact("Ask Corki to do anything")
        child.send("\x1b[I" if focus == "focused" else "\x1b[O")
        child.send("\x1b[200~saved draft\x1b[201~")
        child.expect_exact("Yes, proceed")
        child.setwinsize(26, 100 if width == 40 else 40)
        child.send("y" if decision == "accept" else "\x1b")
        child.expect_exact("APPROVAL_SETTLED")
        child.send("\r")
        child.expect_exact("APPROVAL_NOTIFY_CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
        notifications = re.findall(r"\x1b\]9;([^\a]*)\a", output.getvalue())
        assert notifications == (
            [] if focus == "focused" else ["Corki needs your input", "Corki task complete"]
        )
        assert "\x1b[?1004l" in output.getvalue()
    finally:
        if child.isalive():
            child.terminate(force=True)


PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings
async def main():
    ui = TerminalUI(CorkiSettings(Path.cwd(), notification_method=sys.argv[1]), Path('history'))
    with ui.input_mode():
        task = asyncio.create_task(ui.read_message())
        try:
            async with asyncio.timeout(8):
                while not ui._session.app.is_running or ui._notifications.focused:
                    await asyncio.sleep(.01)
                assert not ui._session.default_buffer.text
                ui.notify('agent-turn-complete', 'turn')
                await asyncio.sleep(.1)
                assert ui._notifications.handle is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert not ui._notifications.pending
    print('NOTIFY_CLEAN_EXIT', flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("method", ["bel", "osc9"])
def test_focus_notification_and_terminal_restore(tmp_path, method):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, method],
        cwd=tmp_path,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1", "TMUX": ""},
        encoding="utf-8",
        dimensions=(24, 80),
        timeout=10,
    )
    try:
        child.expect_exact("\x1b[?1004h")
        child.expect_exact("Ask Corki to do anything")
        child.send("\x1b[O")
        child.expect_exact("\a" if method == "bel" else "\x1b]9;Corki task complete\a")
        child.expect_exact("\x1b[?1004l")
        child.expect_exact("NOTIFY_CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
