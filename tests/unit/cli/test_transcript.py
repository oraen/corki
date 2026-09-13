"""Source replay must neither mutate recorded values nor duplicate recorded output."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.cli.transcript import Transcript
from corki.config import CorkiSettings
from corki.protocol.events import AssistantMessageCompleted, AssistantTextDelta, TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id


def test_plan_source_is_copied_and_rewrapped_without_rerecording(tmp_path):
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=StringIO(), width=40, color_system=None)
    ui._settings = CorkiSettings(working_directory=tmp_path)
    ui._transcript = Transcript(ui)
    step = {"step": "Verify all remaining outputs carefully before finishing", "status": "pending"}
    ui.show_plan((step,), explanation="Retain original source")
    step["step"] = "mutated after publication"
    size = len(ui._transcript.calls)
    original_console = ui._console
    narrow = ui._transcript.render(40)
    wide = ui._transcript.render(100)
    assert "mutated" not in wide
    assert "Verify all remaining outputs carefully before finishing" in wide
    assert "Verify all remaining outputs carefully before finishing" not in narrow
    assert len(ui._transcript.calls) == size
    assert ui._console is original_console
    ui.clear()
    assert "Retain original source" not in ui._transcript.render(100)
    assert "Corki" in ui._transcript.render(100)


@pytest.mark.parametrize("delta", ["obsolete fragment", "Authoritative final text", None])
def test_completed_text_replaces_stream_source_in_real_renderer(delta, tmp_path):
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=StringIO(), width=100, color_system=None),
    )
    app = CorkiApplication.__new__(CorkiApplication)
    app._ui = ui
    thread, turn = new_thread_id(), new_turn_id()

    async def events():
        if delta is not None:
            yield AssistantTextDelta(thread, turn, delta)
        yield AssistantMessageCompleted(thread, turn, "Authoritative final text")
        yield AssistantTextDelta(thread, turn, "second fragment")
        yield AssistantMessageCompleted(thread, turn, "Second final text")
        yield TurnCompleted(thread, turn, "Authoritative final text")

    asyncio.run(app._render_events(events()))
    source = ui._transcript.render(100)
    assert "obsolete fragment" not in source
    assert source.count("Authoritative final text") == 1
    assert "second fragment" not in source
    assert source.count("Second final text") == 1
    assert len(ui._transcript.calls) == 2
