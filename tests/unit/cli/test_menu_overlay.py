import asyncio
from functools import partial
from types import SimpleNamespace

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli import terminal
from corki.cli.approval import choose_approval
from corki.cli.input_owner import InputOwner
from corki.config import CorkiSettings
from corki.protocol.context import ModelContextInfo


@pytest.mark.parametrize("action", ["decline", "withdraw", "close_menu"])
def test_approval_overlays_idle_menu_and_restores_selection(tmp_path, monkeypatch, action):
    with create_pipe_input() as pipe:
        monkeypatch.setattr(
            terminal, "PromptSession", partial(PromptSession, input=pipe, output=DummyOutput())
        )
        settings = CorkiSettings(
            tmp_path, model="Local/First", model_contexts=(ModelContextInfo("Local/Next", 20000),)
        )
        ui = terminal.TerminalUI(settings, tmp_path / "history")

        async def scenario():
            shown = asyncio.Event()

            async def approval(request):
                shown.set()
                return await choose_approval(ui._form_session, execution=True)

            ui.read_elicitation = approval
            owner = InputOwner(ui)
            menu = asyncio.create_task(
                owner.select_model(SimpleNamespace(model=settings.model, reasoning_effort=None))
            )
            pending = None

            def rendered():
                return fragment_list_to_text(to_formatted_text(ui._form_session.message))

            try:
                async with asyncio.timeout(3):
                    while not ui._form_session.app.is_running:
                        await asyncio.sleep(0)
                    pipe.send_text("Local/\x1b[B")
                    while "› Local/Next" not in rendered():
                        await asyncio.sleep(0)
                    pending = asyncio.create_task(owner.elicit(object()))
                    await shown.wait()
                    assert not menu.done()
                    if action == "close_menu":
                        menu.cancel()
                        await asyncio.gather(menu, return_exceptions=True)
                        await asyncio.gather(pending, return_exceptions=True)
                        assert pending.cancelled()
                        assert not ui._form_session.app.is_running
                        assert not owner._lock.locked() and owner._modals == 0
                        assert not ui._transcript.modal_depth
                        return
                    if action == "withdraw":
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                    else:
                        pipe.send_text("d")
                        assert await pending == "decline"
                    while "Select model" not in rendered() or not ui._form_session.app.is_running:
                        await asyncio.sleep(0)
                    assert ui._form_session.default_buffer.text == "Local/"
                    assert "› Local/Next" in rendered()
                    pipe.send_text("\r")
                    assert (await menu).model == "Local/Next"
                    assert not ui._transcript.modal_depth
                    assert not owner._lock.locked() and owner._modals == 0
                    assert not list(ui._form_session.history.get_strings())
            finally:
                for task in (menu, pending):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(t for t in (menu, pending) if t is not None), return_exceptions=True
                )

        asyncio.run(scenario())
