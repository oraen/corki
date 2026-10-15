import asyncio
from functools import partial

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli import terminal
from corki.cli.input_owner import InputOwner
from corki.config import CorkiSettings


@pytest.mark.parametrize("cancel_pending", [False, True])
@pytest.mark.parametrize("input_text", ["draft", "\x1b[200~draft\x1b[201~"])
def test_typing_delays_approval_and_preserves_draft(
    tmp_path, monkeypatch, cancel_pending, input_text
):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        ui = terminal.TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")

        async def scenario():
            shown = asyncio.Event()
            release = asyncio.Event()

            async def approval(request):
                shown.set()
                await release.wait()
                return "decline", None

            ui.read_elicitation = approval
            owner = InputOwner(ui)
            reader = asyncio.create_task(owner.read_message())
            pending = None
            try:
                async with asyncio.timeout(5):
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text(input_text)
                    while ui._session.default_buffer.text != "draft":
                        await asyncio.sleep(0)
                    pending = asyncio.create_task(owner.elicit(object()))
                    await asyncio.sleep(0.2)
                    assert not shown.is_set(), "approval stole active typing"
                    pipe.send_text("y")
                    while ui._session.default_buffer.text != "drafty":
                        await asyncio.sleep(0)
                    # The new character must restart the idle deadline.
                    await asyncio.sleep(0.85)
                    assert not shown.is_set()
                    if cancel_pending:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                        assert not owner._modals
                    else:
                        await shown.wait()
                        assert ui._draft == "drafty"
                        release.set()
                        assert await pending == ("decline", None)
                    while not ui._session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("\r")
                    assert await reader == "drafty"
                    assert not list(ui._form_session.history.get_strings())
                    assert owner._modals == 0
            finally:
                release.set()
                for task in (reader, pending):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(t for t in (reader, pending) if t is not None), return_exceptions=True
                )

        asyncio.run(scenario())
