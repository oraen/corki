"""Exercise the actual TerminalUI approval and composer on a real terminal."""

import os
import sys

import pexpect
import pytest

PROGRAM = """
import asyncio
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings
from corki.mcp.elicitation import ElicitationRequest

async def main():
    ui = TerminalUI(CorkiSettings(working_directory=Path.cwd()), Path('input-history'))
    request = ElicitationRequest('local-shell', 'fixture', {
        'message': 'Review this fixture operation before proceeding.',
        '_meta': {'tool_params': {'command': 'echo ' + 'long_argument_' * 20}},
        'requestedSchema': {'type': 'object', 'properties': {}},
    }, 'shell_approval')
    result = await ui.read_elicitation(request)
    assert ui._form_session.default_buffer.text == ''
    assert list(ui._form_session.history.get_strings()) == []
    print('DECISION=' + result[0], flush=True)
    message = await ui.read_message()
    assert message == 'ordinary followup'
    assert list(ui._session.history.get_strings()) == ['ordinary followup']
    print('COMPOSER_RESTORED', flush=True)

asyncio.run(main())
"""

CONCURRENT_PROGRAM = """
import asyncio
import sys
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.cli.input_owner import InputOwner
from corki.config import CorkiSettings
from corki.mcp.elicitation import ElicitationRequest

async def main():
    ui = TerminalUI(CorkiSettings(working_directory=Path.cwd()), Path('input-history'))
    owner = InputOwner(ui)
    composer = asyncio.create_task(owner.read_message())
    while ui._session.default_buffer.text != 'saved draft':
        await asyncio.sleep(0.01)
    if sys.argv[2] == 'history':
        while not ui._session.app.renderer._in_alternate_screen:
            await asyncio.sleep(0.01)

    def request(identity):
        return ElicitationRequest('local-shell', identity, {
            'message': identity,
            '_meta': {'tool_params': {'command': 'echo ' + identity}},
            'requestedSchema': {'type': 'object', 'properties': {}},
        }, 'shell_approval')

    first = asyncio.create_task(owner.elicit(request('FIRST_REQUEST')))
    second = asyncio.create_task(owner.elicit(request('SECOND_REQUEST')))
    while owner._modals != 2:
        await asyncio.sleep(0.01)
    if sys.argv[1] == 'queued_cancel':
        second.cancel()
        try:
            await second
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('queued cancellation was swallowed')
    assert (await first)[0] == 'accept'
    assert not ui._history_view.active
    assert not ui._session.app.renderer.full_screen
    assert not ui._session.app.renderer._in_alternate_screen
    if sys.argv[1] != 'queued_cancel':
        assert (await second)[0] == sys.argv[1]
    assert owner._modals == 0
    assert ui._form_session.default_buffer.text == ''
    assert list(ui._form_session.history.get_strings()) == []
    print('ALL_DECISIONS_SETTLED', flush=True)
    assert await composer == 'saved draft followup'
    assert list(ui._session.history.get_strings()) == ['saved draft followup']
    assert owner._reader is None
    print('DRAFT_AND_HISTORY_RESTORED', flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("columns", [40, 100])
@pytest.mark.parametrize("close_key", ["q", "\x03"])
@pytest.mark.parametrize("kind", ["shell", "patch"])
def test_full_approval_details_can_be_read_without_deciding(tmp_path, columns, close_key, kind):
    program = PROGRAM.replace(
        "'long_argument_' * 20", "'long_argument_' * 2000 + 'TAILBEYONDLIMIT'"
    ).replace(
        "Review this fixture operation before proceeding.",
        "DETAILSBEGIN Review this fixture operation before proceeding.",
    )
    if kind == "patch":
        program = (
            program.replace("'shell_approval'", "'patch_approval'")
            .replace(
                "'command': 'echo '", "'patch': '*** Begin Patch\\n*** Add File: review.txt\\n+'"
            )
            .replace(
                "'_meta': {'tool_params':",
                "'_meta': {'patch_retry': {'sandbox': 'seatbelt', 'output': 'RISKREVIEWREQUIRED', "
                "'execution': {'stdout': '', 'stderr': 'permission denied'}, "
                "'committed_delta': {'exact': False, 'changes': []}}, 'tool_params':",
            )
        )
    child = pexpect.spawn(
        sys.executable,
        ["-c", program],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=8,
        dimensions=(24, columns),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    try:
        child.expect("Ctrl\\+A to view full details")
        child.send("\x1b[B\x01")
        child.expect("Approval details")
        if kind == "patch":
            child.expect("RISKREVIEWREQUIRED")
        child.send("y1\r\x1b[200~q\x03\x1b[201~")
        assert child.expect(["DECISION=", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("\x1b[F")
        child.expect("TAILBEYONDLIMIT")
        child.setwinsize(20, 100 if columns == 40 else 40)
        child.send("\x1b[H")
        child.expect("DETAILSBEGIN")
        child.send("\x1b[F")
        child.expect("TAILBEYONDLIMIT")
        child.send(close_key)
        child.expect("Ctrl\\+A to view full details")
        assert child.expect(["DECISION=", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("\r")
        child.expect("DECISION=decline")
        child.expect("Ask Corki to do anything")
        child.send("ordinary followup\r")
        child.expect("COMPOSER_RESTORED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)


@pytest.mark.parametrize("columns", [40, 100])
@pytest.mark.parametrize("scope", ["once", "session", "rule"])
@pytest.mark.parametrize("input_method", ["arrows", "letter", "number"])
def test_execution_scope_is_a_single_keyboard_decision(tmp_path, columns, scope, input_method):
    program = PROGRAM.replace(
        "'properties': {}",
        "'properties': {'scope': {'type': 'string', "
        "'enum': ['once', 'session', 'rule'], 'default': 'once'}}",
    ).replace(
        "    assert ui._form_session.default_buffer.text == ''",
        f"    assert result == ('accept', {{'scope': {scope!r}}}), result\n"
        "    assert ui._form_session.default_buffer.text == ''",
    )
    child = pexpect.spawn(
        sys.executable,
        ["-c", program],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=8,
        dimensions=(30, columns),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    try:
        child.expect("Shell execution approval")
        child.expect("Yes, proceed once")
        child.send("\x1b[200~1\n2\n3\ny\na\np\n\x1b[201~")
        assert child.expect(["DECISION=", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("\x1b[B" * (1 if scope == "session" else 2) if scope != "once" else "\x1b[A")
        assert child.expect(["DECISION=", pexpect.TIMEOUT], timeout=0.2) == 1
        keys = {
            "arrows": "\r",
            "letter": {"once": "y", "session": "a", "rule": "p"}[scope],
            "number": {"once": "1", "session": "2", "rule": "3"}[scope],
        }
        child.send(keys[input_method])
        child.expect("DECISION=accept")
        child.expect("Ask Corki to do anything")
        child.send("ordinary followup\r")
        child.expect("COMPOSER_RESTORED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


@pytest.mark.parametrize("columns", [40, 100])
@pytest.mark.parametrize(
    "keys,decision",
    [
        ("\r", "accept"),
        ("\x1b[B\r", "decline"),
        ("\x03", "cancel"),
        ("d", "decline"),
        ("n", "cancel"),
        ("2", "decline"),
        ("3", "cancel"),
    ],
)
def test_real_terminal_approval_is_explicit_and_restores_composer(
    tmp_path, columns, keys, decision
):
    environment = {**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"}
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        timeout=8,
        dimensions=(30, columns),
    )
    try:
        child.expect("Shell execution approval")
        child.expect("Yes, proceed")
        child.send("IGNORED_CONFIRMATION_TEXT")
        assert child.expect(["DECISION=", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send("\x1b]11;rgb:ffff/")
        assert child.expect(["DECISION=", pexpect.TIMEOUT], timeout=0.1) == 1
        child.send("ffff/ffff\x07\x1b]10;rgb:00/00/00\x1b\\")
        assert child.expect(["DECISION=", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send(keys)
        child.expect("DECISION=" + decision)
        child.expect("Ask Corki to do anything")
        child.send("ordinary\x1b]11;rgb:ffff/ffff/ffff\x07 followup\r")
        child.expect("COMPOSER_RESTORED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)


@pytest.mark.parametrize("columns", [40, 100])
@pytest.mark.parametrize("second", ["decline", "cancel", "queued_cancel"])
@pytest.mark.parametrize("history_open", [False, True])
def test_real_terminal_concurrent_approvals_preserve_draft_and_decision_identity(
    tmp_path, columns, second, history_open
):
    environment = {**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"}
    child = pexpect.spawn(
        sys.executable,
        ["-c", CONCURRENT_PROGRAM, second, "history" if history_open else "composer"],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        timeout=8,
        dimensions=(30, columns),
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("saved draft")
        if history_open:
            child.sendcontrol("t")
            child.expect_exact("\x1b[?1049h")
            child.expect_exact("\x1b[?1049l")
        child.expect("FIRST_REQUEST")
        child.expect("Yes, proceed")
        assert (
            child.expect(["SECOND_REQUEST", "ALL_DECISIONS_SETTLED", pexpect.TIMEOUT], timeout=0.2)
            == 2
        )
        child.send("\r")
        if second != "queued_cancel":
            child.expect("SECOND_REQUEST")
            child.expect("Yes, proceed")
            assert child.expect(["ALL_DECISIONS_SETTLED", pexpect.TIMEOUT], timeout=0.2) == 1
            child.send("\x1b[B\r" if second == "decline" else "\x03")
        else:
            assert child.expect(["SECOND_REQUEST", "ALL_DECISIONS_SETTLED"]) == 1
        if second != "queued_cancel":
            child.expect("ALL_DECISIONS_SETTLED")
        child.send(" followup\r")
        child.expect("DRAFT_AND_HISTORY_RESTORED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)


@pytest.mark.parametrize("columns", [40, 100])
@pytest.mark.parametrize(
    "keys,decision", [("\r", "accept"), ("\x1b[B\r", "decline"), ("\x03", "cancel")]
)
@pytest.mark.parametrize("height_only", [False, True])
def test_resize_waits_for_explicit_modal_decision(tmp_path, columns, keys, decision, height_only):
    program = PROGRAM.replace(
        "    result = await ui.read_elicitation(request)",
        "    ui.show_notice('OLD_TRANSCRIPT')\n"
        "    watcher = asyncio.create_task(ui.watch_resize())\n"
        "    result = await ui.read_elicitation(request)",
    ).replace(
        "    print('COMPOSER_RESTORED', flush=True)",
        "    watcher.cancel()\n"
        "    await asyncio.gather(watcher, return_exceptions=True)\n"
        "    source = ui._transcript.render(100)\n"
        "    assert 'IGNORED_CONFIRMATION_TEXT' not in source\n"
        "    assert 'Review this fixture operation' not in source\n"
        "    assert 'ordinary followup' in source\n"
        "    assert ui._transcript.modal_depth == 0\n"
        "    print('COMPOSER_RESTORED', flush=True)",
    )
    child = pexpect.spawn(
        sys.executable,
        ["-c", program],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=8,
        dimensions=(30, columns),
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    try:
        child.expect("OLD_TRANSCRIPT")
        child.expect("Shell execution approval")
        child.expect("Yes, proceed")
        child.setwinsize(35, columns if height_only else 100 if columns == 40 else 40)
        child.send("IGNORED_CONFIRMATION_TEXT")
        # The debounce has time to expire, but no replay/decision may occur inside the modal.
        assert child.expect(["OLD_TRANSCRIPT", "DECISION=", pexpect.TIMEOUT], timeout=0.3) == 2
        child.send(keys)
        child.expect("DECISION=" + decision)
        # The pending resize is not lost when the modal releases terminal ownership.
        child.expect("OLD_TRANSCRIPT")
        child.expect("Ask Corki to do anything")
        child.send("ordinary followup\r")
        child.expect("COMPOSER_RESTORED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
