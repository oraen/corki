"""Actual partial native writes remain visible during read-only retry review."""

import asyncio
from io import StringIO

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console
from test_bundled_execution import compiler as compiler
from test_execution_approval_cancel import observe
from test_patch_approvals import Model, create_runtime, patch

from corki.cli.application import CorkiApplication
from corki.cli.approval_details import ApprovalDetails
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("decision", ["decline", "cancel"])
def test_long_partial_patch_evidence_survives_runtime_retry_review(
    tmp_path, compiler, mode, decision
):
    async def scenario(pipe):
        root = (tmp_path / "workspace").resolve()
        root.mkdir()
        outside = tmp_path.resolve() / "outside.txt"
        text = "long committed content " * 1000 + "COMMITTEDEVIDENCETAIL"
        model = Model(mode)
        model.patch = patch(f"*** Add File: first.txt\n+{text}\n*** Add File: {outside}\n+outside")
        runtime = create_runtime(root, compiler, model)
        requests = []
        settings = CorkiSettings(root)

        class UI(TerminalUI):
            async def read_elicitation(self, request):
                requests.append(request)
                evidence = request.params["_meta"].get("patch_retry")
                if evidence is None:
                    assert not (root / "first.txt").exists()
                    return "accept", {"scope": "once"}
                assert (root / "first.txt").read_text() == text + "\n"
                assert not outside.exists()
                assert evidence["execution"]["sandbox_denied"] is True
                assert evidence["committed_delta"]["exact"] is False
                assert evidence["committed_delta"]["changes"][0]["change"]["content"] == text + "\n"
                return await super().read_elicitation(request)

        ui = UI(settings, tmp_path / "input-history", console=Console(file=StringIO()))
        ui._form_session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        ui._form_session.app.ttimeoutlen = 0.01
        original_layout = ui._form_session.layout.container
        ready = asyncio.Event()
        prompt = ui._form_session.prompt_async

        async def started(*args, **kwargs):
            return await prompt(*args, **kwargs, pre_run=ready.set)

        ui._form_session.prompt_async = started
        CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "host"), runtime, ui)
        task = asyncio.create_task(observe(runtime, "apply patch and review a denied retry"))
        try:
            async with asyncio.timeout(15):
                await ready.wait()
                pipe.send_text("\x01")
                while not isinstance(ui._form_session.layout.current_control, ApprovalDetails):
                    await asyncio.sleep(0.01)
                pager = ui._form_session.layout.current_control
                evidence_text = pager.content.split("Tool arguments:")[0]
                assert "COMMITTEDEVIDENCETAIL" in evidence_text
                assert '"exact": false' in evidence_text
                assert len(evidence_text) > 12000
                pipe.send_text("y1\r\x1b[200~q\x03\x1b[201~\x1b[F")
                while pager.row != len(pager.lines) - 1:
                    await asyncio.sleep(0.01)
                assert pager.active and not task.done()
                assert not outside.exists()
                pipe.send_text("q")
                while pager.active:
                    await asyncio.sleep(0.01)
                pipe.send_text("d" if decision == "decline" else "n")
                events = await task
            terminal = [
                e for e in events if isinstance(e, (TurnCompleted, TurnCancelled, TurnFailed))
            ]
            assert len(terminal) == 1
            assert isinstance(
                terminal[0], TurnCompleted if decision == "decline" else TurnCancelled
            )
            assert len(requests) == 2 and requests[0].request_id != requests[1].request_id
            assert not outside.exists()
            assert (root / "first.txt").read_text() == text + "\n"
            assert not runtime._process_manager.approvals.router._pending
            assert not runtime._process_manager.approvals._session
            assert ui._form_session.layout.container is original_layout
            assert not ui._form_session.app.renderer._in_alternate_screen
            assert not list(ui._form_session.history.get_strings())
            assert ui._form_session.default_buffer.text == ""
            assert ui._transcript.modal_depth == 0
            if mode == "code_mode_only":
                assert not runtime._code_mode.cells
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
