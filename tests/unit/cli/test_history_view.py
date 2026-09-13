import asyncio
from io import StringIO

import pytest
from prompt_toolkit.document import Document
from rich.console import Console

from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings


def test_history_view_owns_focus_and_tracks_bottom_without_mutating_draft(tmp_path):
    async def scenario():
        ui = TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        ui._session.default_buffer.document = Document("draft", 2)
        ui.show_assistant_message("first")
        previous = ui._session.layout.current_control
        view = ui._history_view
        view.open()
        assert ui._session.app.full_screen and ui._session.app.renderer.full_screen
        assert view.active and ui._session.layout.current_control is view.control
        ui.show_assistant_message("second")
        view.text()
        assert view.row == view.rows - 1
        view.row = 0
        ui.show_assistant_message("third")
        view.text()
        assert view.row == 0
        view.close()
        assert not ui._session.app.full_screen and not ui._session.app.renderer.full_screen
        assert not view.active and ui._session.layout.current_control is previous
        assert ui._session.default_buffer.document == Document("draft", 2)

    asyncio.run(scenario())


def test_history_defers_display_writes_and_repair_until_close(tmp_path, monkeypatch):
    async def scenario():
        output = StringIO()
        ui = TerminalUI(
            CorkiSettings(tmp_path),
            tmp_path / "history",
            console=Console(file=output, force_terminal=True),
        )
        written = []
        monkeypatch.setattr(ui._session.app.output, "write_raw", written.append)
        view = ui._history_view
        view.open()
        ui.show_notice("notice while browsing")
        ui.begin_assistant_message()
        await ui.append_assistant_delta_live("answer while browsing\n\n")
        ui.end_assistant_message()
        assert output.getvalue() == ""
        assert "answer while browsing" in ui._transcript.render(80)
        ui._transcript.repair_pending = True
        await ui._transcript.repair()
        assert written == []
        assert ui._transcript.repair_pending
        view.close()
        text = "".join(written)
        assert text.count("notice while browsing") == 1
        assert text.count("answer while browsing") == 1
        view.close()
        assert "".join(written) == text

    asyncio.run(scenario())


def test_cancelled_reader_closes_history_and_preserves_draft(tmp_path, monkeypatch):
    async def scenario():
        ui = TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        entered = asyncio.Event()
        written = []
        monkeypatch.setattr(ui._session.app.output, "write_raw", written.append)

        async def prompt(*args, **kwargs):
            ui._session.default_buffer.document = Document("unfinished draft", 3)
            ui._history_view.open()
            ui.show_notice("notice before cancellation")
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(ui._session, "prompt_async", prompt)
        task = asyncio.create_task(ui.read_message())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not ui._history_view.active
        assert not ui._session.app.full_screen and not ui._session.app.renderer.full_screen
        assert ui._draft == "unfinished draft"
        assert ui._session.layout.current_control is not ui._history_view.control
        assert "".join(written).count("notice before cancellation") == 1
        assert ui._history_view.deferred_output == []

    asyncio.run(scenario())
