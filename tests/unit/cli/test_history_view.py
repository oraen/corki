import asyncio
from io import StringIO

import pytest
from prompt_toolkit.document import Document
from rich.console import Console

from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings


def plain(view):
    return "".join(text for _, text in view.text())


@pytest.mark.parametrize("limit", ["characters", "chunks"])
def test_deferred_limit_switches_to_canonical_redraw_without_losing_output(
    tmp_path, monkeypatch, limit
):
    import corki.cli.history_view as module

    async def scenario():
        ui = TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        monkeypatch.setattr(
            module, "MAX_DEFERRED_OUTPUT_CHARS", 80 if limit == "characters" else 100000
        )
        monkeypatch.setattr(module, "MAX_DEFERRED_OUTPUT_CHUNKS", 2 if limit == "chunks" else 4096)
        written = []
        monkeypatch.setattr(ui._session.app.output, "write_raw", written.append)
        view = ui._history_view
        view.open()
        for index in range(20):
            ui.show_notice(f"UNIQUE-NOTICE-{index:02} 中文内容")
            assert view._deferred_chars <= module.MAX_DEFERRED_OUTPUT_CHARS
            assert len(view.deferred_output) <= module.MAX_DEFERRED_OUTPUT_CHUNKS
        assert view._deferred_overflow and ui._transcript.repair_pending
        assert not view.deferred_output
        assert written == []
        view.close()
        assert not view._deferred_overflow and view._deferred_chars == 0
        assert ui._transcript.repair_pending
        assert written == []  # don't publish a partial suffix before the full repair
        replay = ui._transcript.render(80)
        for index in range(20):
            assert replay.count(f"UNIQUE-NOTICE-{index:02}") == 1
        view.open()
        visible = plain(view)
        for index in range(20):
            assert visible.count(f"UNIQUE-NOTICE-{index:02}") == 1
        view.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["open", "close"])
def test_renderer_failure_restores_modes_focus_and_releases_all_stores(
    tmp_path, monkeypatch, phase
):
    async def scenario():
        ui = TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        ui.show_tool_output("preserved full source\n" * 50)
        view, app = ui._history_view, ui._session.app
        modes = app.full_screen, app.renderer.full_screen
        focus = ui._session.layout.current_control
        source = tuple(ui._transcript.calls)
        view.text()
        stores = tuple(view._stores)
        erase = app.renderer.erase
        if phase == "close":
            view.open()

        def fail(*args, **kwargs):
            raise OSError("terminal disconnected during erase")

        monkeypatch.setattr(app.renderer, "erase", fail)
        with pytest.raises(OSError, match="during erase"):
            getattr(view, phase)()
        assert not view.active and not view._stores
        assert all(store.closed and store._data.closed and store._index.closed for store in stores)
        assert (app.full_screen, app.renderer.full_screen) == modes
        assert ui._session.layout.current_control is focus
        assert tuple(ui._transcript.calls) == source
        assert view.previous_focus is None and view.previous_screen_modes is None
        monkeypatch.setattr(app.renderer, "erase", erase)
        view.open()
        assert plain(view).count("preserved full source") == 50
        view.close()

    asyncio.run(scenario())


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


@pytest.mark.parametrize("write_fails", [False, True])
def test_close_releases_render_cache_and_reopen_keeps_complete_history(
    tmp_path, monkeypatch, write_fails
):
    async def scenario():
        ui = TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        ui.show_tool_output("first\n" + "payload\n" * 500 + "last")
        source = tuple(ui._transcript.calls)
        view = ui._history_view
        previous = ui._session.layout.current_control
        view.open()
        assert "first" in plain(view) and "last" in plain(view)
        assert view.cache_key is not None and len(view.lines) > 500
        ui.show_notice("pending notice")
        if write_fails:

            def fail(text):
                raise OSError("terminal disconnected")

            monkeypatch.setattr(ui._session.app.output, "write_raw", fail)
            with pytest.raises(OSError, match="disconnected"):
                view.close()
        else:
            view.close()
        assert not view.active
        assert ui._session.layout.current_control is previous
        assert not view._stores and view.lines == [[]]
        assert view.cache_key is None and view.deferred_output == []
        assert view.previous_focus is None and view.previous_screen_modes is None
        assert tuple(ui._transcript.calls[: len(source)]) == source
        # Closing twice must not try to flush failed terminal output again.
        view.close()
        monkeypatch.setattr(ui._session.app.output, "write_raw", lambda text: None)
        view.open()
        assert plain(view).count("payload") == 500
        assert "first" in plain(view) and "last" in plain(view)
        assert "pending notice" in plain(view)
        view.close()

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


def test_display_capacity_failure_is_visible_preserves_source_and_closes_files(
    tmp_path, monkeypatch
):
    import corki.cli.history_view as module
    from corki.cli.history_rows import HistoryRows

    async def scenario():
        ui = TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        ui.show_tool_output("retained source\n" * 100)
        source = tuple(ui._transcript.calls)
        created = []

        def small_store(**kwargs):
            store = HistoryRows(max_bytes=100)
            created.append(store)
            return store

        monkeypatch.setattr(module, "HistoryRows", small_store)
        view = ui._history_view
        view.open()
        assert "History display unavailable" in plain(view)
        assert len(created) == 1 and created[0].closed
        assert tuple(ui._transcript.calls) == source and not ui._transcript.replaying
        assert not view._stores
        view.close()
        monkeypatch.setattr(module, "HistoryRows", HistoryRows)
        view.open()
        assert plain(view).count("retained source") == 100
        stores = tuple(view._stores)
        view.close()
        assert all(store.closed for store in stores)

    asyncio.run(scenario())
