"""Real terminal question response, ordinary draft recovery and narrow widths."""

import os
import sys

import pexpect
import pytest

BOOTSTRAP = r"""
import asyncio, json, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall

class Model:
    count = 0
    async def stream(self, request):
        self.count += 1
        turn, step = request.items[-1].turn_id, new_step_id()
        if self.count == 1:
            call = ToolCall("ask", "request_user_input", {"questions": [{
                "id": "scope", "header": "Scope", "question": "Choose the implementation scope.",
                "options": [{"label": "Small", "description": "Only the main fix."},
                            {"label": "Large", "description": "Include supporting changes."}],
            }]})
            yield ModelCompleted((ToolCallItem(call, turn, step),))
        else:
            result = next(i for i in reversed(request.items) if isinstance(i, ToolResultItem))
            assert not result.is_error
            assert json.loads(result.content) == {"answers": {"scope": {"answers": ["Large"]}}}
            yield ModelCompleted((AssistantMessageItem("Answer received.", turn, step),))
    async def aclose(self): pass

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(working_directory=cwd, skills_enabled=False,
                            plugins_enabled=False, collaboration_mode="plan",
                            realtime_enabled=sys.argv[1] == 'true')
    runtime = await LangGraphRuntime.acreate(
        settings=settings, database_path=cwd / "session.db", model=Model()
    )
    ui = TerminalUI(settings, cwd / "input-history")
    ui._draft = "retained-draft"
    app = CorkiApplication(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    try:
        await app._consume_turn("ask")
        assert ui._draft == "retained-draft", repr(ui._draft)
        assert ui._form_session.default_buffer.text == ""
        assert not list(ui._form_session.history.get_strings())
        assert ui._transcript.modal_depth == 0
    finally:
        await runtime.aclose()
    print("RESULT_OK", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("realtime", ["true", "false"])
def test_question_answer_and_draft_recovery_in_real_terminal(tmp_path, width, realtime):
    child = pexpect.spawn(
        sys.executable,
        ["-c", BOOTSTRAP, realtime],
        cwd=str(tmp_path),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
        dimensions=(30, width),
        encoding="utf-8",
        timeout=20,
    )
    try:
        child.expect_exact("Question 1/1")
        child.expect_exact("tab to")
        with pytest.raises(pexpect.TIMEOUT):
            child.expect_exact("RESULT_OK", timeout=0.15)
        child.send("2")
        child.expect_exact("RESULT_OK")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
