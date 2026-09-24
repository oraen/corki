import os
import sys
import time

import pexpect
import pytest

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.session_catalog import SessionCatalog
from corki.cli.session_picker import choose_session
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem, AssistantMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository
async def main():
    cwd = Path.cwd()
    repo = SQLiteSessionRepository(cwd / 'test.db')
    target = new_thread_id()
    await repo.create_thread(target, cwd)
    await repo.append_items(target, (UserMessageItem('resume-target', new_turn_id()),))
    answer = AssistantMessageItem('TRANSCRIPT_BODY', new_turn_id(), new_step_id())
    await repo.append_items(target, (answer,))
    await repo.close()
    result = await choose_session(SessionCatalog(cwd / 'test.db'), cwd)
    assert result == (str(target) if sys.argv[1] in {'select', 'transcript'} else None)
    print('PICKER_CLEAN_EXIT', flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("mode", ["select", "cancel", "transcript"])
def test_picker_search_select_or_cancel(tmp_path, width, mode):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, mode],
        cwd=tmp_path,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
        encoding="utf-8",
        dimensions=(24, width),
        timeout=10,
    )
    try:
        child.expect_exact("resume-target")
        if mode == "transcript":
            child.sendcontrol("t")
            child.expect_exact("History")
            child.expect_exact("TRANSCRIPT_BODY")
            child.send("q")
            child.expect_exact("Resume a session")
            child.send("\r")
        elif mode == "select":
            child.send("target")
            time.sleep(0.2)
            child.send("\r")
        else:
            child.sendcontrol("c")
        child.expect_exact("PICKER_CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
