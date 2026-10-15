import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.backtrack import editable_prompts, prompt_input
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    calls = []
    class Model:
        async def stream(self, request):
            calls.append(request)
            answer = AssistantMessageItem(
                'ANSWER_CONTEXT', request.items[-1].turn_id, new_step_id())
            yield ModelCompleted((answer,))
        async def aclose(self):
            pass
    async def create(**kwargs):
        return await LangGraphRuntime.acreate(
            settings=settings, model=Model(), database_path=cwd/'state.db',
            home_path=cwd/'home', **kwargs)
    source = await create()
    target = None
    try:
        for text in ('FIRST_中文\n第二行', 'LATEST_PROMPT'):
            async for _ in source.stream(text):
                pass
        original = await source.load_display_snapshot()
        ui = TerminalUI(settings, cwd/'history')
        app = CorkiApplication(settings, CorkiPaths.from_home(cwd/'home'), source, ui)
        async def branch(thread, index, prompt):
            nonlocal target
            target = await create(fork_from_thread_id=thread, fork_before_user_message=index)
            await target.load_display_snapshot()
            ui.restore_queued_inputs((prompt_input(prompt),))
            return object()
        app._branch_factory = branch
        selected = await app._edit_previous_prompt()
        assert len(calls) == 2
        assert await source.load_display_snapshot() == original
        if sys.argv[1] == 'select':
            assert selected and target is not None
            assert ui._draft == 'FIRST_中文\n第二行'
            assert not editable_prompts(await target.load_display_snapshot())
            assert [event async for event in target.resume_pending()] == []
        else:
            assert not selected and target is None and not ui._draft
        assert len(calls) == 2
    finally:
        if target is not None:
            await target.aclose()
        await source.aclose()
    print('BACKTRACK_CLEAN_EXIT', flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("mode", ["select", "cancel"])
def test_transcript_selection_forks_without_mutating_source(tmp_path, width, mode):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, mode],
        cwd=tmp_path,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
        encoding="utf-8",
        dimensions=(24, width),
        timeout=15,
    )
    try:
        child.expect_exact("ANSWER_CONTEXT")
        child.expect_exact("Edit previous prompt")
        child.setwinsize(20, 100 if width == 40 else 40)
        if mode == "select":
            child.send("\x1b[D")
            child.send("\r")
        else:
            child.send("q")
        child.expect_exact("BACKTRACK_CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
