"""Final plan cells have their own header and source-backed width reflow."""

import asyncio
from io import StringIO

import pytest
from prompt_toolkit.data_structures import Size
from rich.console import Console

from corki.cli.terminal import TerminalUI
from corki.cli.transcript import Transcript
from corki.config import CorkiSettings


@pytest.mark.parametrize(
    ("width", "body"),
    [
        (
            40,
            [
                "  Inspect the current files and verify",
                "  all remaining behavior before",
                "  changing runtime ownership.",
            ],
        ),
        (
            100,
            [
                "  Inspect the current files and verify all remaining behavior "
                "before changing runtime ownership."
            ],
        ),
    ],
)
def test_completed_plan_layout_snapshot(width, body):
    output = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=output, width=width, color_system=None)
    ui.show_proposed_plan(
        "Inspect the current files and verify all remaining behavior "
        "before changing runtime ownership."
    )
    assert [line.rstrip() for line in output.getvalue().splitlines()] == [
        "• Proposed Plan",
        "",
        "",
        *body,
        "",
    ]


def test_empty_plan_is_explicit_and_not_an_updated_plan_checklist():
    output = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=output, width=40, color_system=None)
    ui.show_proposed_plan("")
    assert "  (empty)" in output.getvalue()
    assert "Updated Plan" not in output.getvalue()


def test_plan_transcript_reflows_source_without_changing_saved_calls():
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=StringIO(), width=40, color_system=None)
    ui._transcript = Transcript(ui)
    text = "Inspect the current files and verify all remaining behavior before changing ownership."
    ui.show_proposed_plan(text)
    saved = list(ui._transcript.calls)
    narrow, wide = ui._transcript.render(40), ui._transcript.render(100)
    assert text not in narrow and text in wide
    assert narrow.count("Proposed Plan") == wide.count("Proposed Plan") == 1
    assert ui._transcript.calls == saved


@pytest.mark.parametrize("committed", [0, 1])
def test_interrupted_plan_replay_never_promotes_queued_rows(tmp_path, committed):
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "h",
        console=Console(file=StringIO(), force_terminal=True, width=40),
    )
    ui.enable_stream_animation()
    ui.begin_proposed_plan()
    ui.append_proposed_plan_delta("- committed row\n- hidden row\n")
    assert ui._plan_stream.queued_lines == 2
    ui._plan_stream.drain(ui._console, committed)
    ui.end_proposed_plan()
    replay = ui._transcript.render(100)
    assert "hidden row" not in replay
    assert ("committed row" in replay) is bool(committed)
    assert ui._plan_stream is None


def test_combined_plan_and_answer_pressure_triggers_one_shared_catchup(tmp_path):
    async def scenario():
        ui = TerminalUI(
            CorkiSettings(tmp_path),
            tmp_path / "h",
            console=Console(file=StringIO(), force_terminal=True, width=40),
        )
        ui.enable_stream_animation()
        ui.begin_assistant_message()
        ui.append_assistant_delta("".join(f"- answer {i}\n" for i in range(4)))
        owner = ui.stream_animation_owner()
        ui.begin_proposed_plan()
        ui.append_proposed_plan_delta("".join(f"- plan {i}\n" for i in range(4)))
        assert ui.stream_animation_owner() is owner
        assert ui._stream_markdown.queued_lines == ui._plan_stream.queued_lines == 4
        await ui.commit_stream_tick(catch_up_only=True)
        assert ui._stream_markdown.emitted == ui._plan_stream.emitted == 4
        assert ui.stream_animation_owner() is None
        ui.end_proposed_plan()
        ui.end_assistant_message()

    asyncio.run(scenario())


def test_live_plan_history_tracks_commits_without_draining_queue(tmp_path):
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "h",
        console=Console(file=StringIO(), force_terminal=True, width=40),
    )
    ui.enable_stream_animation()
    ui.begin_proposed_plan()
    ui.append_proposed_plan_delta("- committed row\n- hidden row\n")
    stream = ui._plan_stream
    saved = list(ui._transcript.calls)
    for committed in (0, 1, 2):
        if committed:
            stream.drain(ui._console, 1)
        for width in (40, 100):
            replay = ui._transcript.render(width)
            assert ("committed row" in replay) is (committed >= 1)
            assert ("hidden row" in replay) is (committed == 2)
            assert ui._plan_stream is stream
            assert stream.queued_lines == 2 - committed
            assert ui._transcript.calls == saved
        from prompt_toolkit import ANSI
        from prompt_toolkit.formatted_text import to_formatted_text

        width = ui._session.app.output.get_size().columns
        committed_text = ui._transcript.render(width, include_reasoning=True, expand_tools=True)
        expected = "".join(p[1] for p in to_formatted_text(ANSI(committed_text)))
        expected += "".join(p[1] for p in ui._plan_tail_fragments())
        assert "".join(p[1] for p in ui._history_view.text()) == expected
        assert stream.queued_lines == 2 - committed and ui._transcript.calls == saved
    ui._history_view.close()
    ui.end_proposed_plan()


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("prefix", ["", "- stable row\n\n"])
def test_plan_table_tail_is_preview_only_and_disappears_on_interruption(
    tmp_path, monkeypatch, prefix, width
):
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "h",
        console=Console(file=StringIO(), force_terminal=True, width=40),
    )
    ui.enable_stream_animation()
    monkeypatch.setattr(ui._session.app.output, "get_size", lambda: Size(rows=30, columns=width))
    ui.begin_proposed_plan()
    ui.append_proposed_plan_delta(
        prefix + "| Name | Value |\n| --- | --- |\n| alpha | beta |\n| PARTIAL"
    )
    stream = ui._plan_stream
    if stream.queued_lines:
        assert ui._stream_tail_fragments() == []
        stream.drain(ui._console, stream.queued_lines)
    saved = list(ui._transcript.calls)
    visible = "".join(part[1] for part in ui._stream_tail_fragments())
    assert "alpha" in visible and "beta" in visible
    assert ("Proposed Plan" in visible) is (not prefix)
    assert "stable row" not in visible
    assert "PARTIAL" not in visible
    assert "alpha" not in ui._transcript.render(40)
    assert ui._transcript.calls == saved
    history = "".join(part[1] for part in ui._history_view.text())
    assert "alpha" in history and "beta" in history
    assert "PARTIAL" not in history
    assert history.count("Proposed Plan") == 1
    assert ui._transcript.calls == saved
    assert stream.queued_lines == 0
    ui.append_proposed_plan_delta(" | gamma |\n")
    updated = "".join(part[1] for part in ui._history_view.text())
    assert "PARTIAL" in updated and "gamma" in updated
    assert updated.count("alpha") == 1
    assert stream.queued_lines == 0
    assert "gamma" not in ui._transcript.render(width)
    ui.end_proposed_plan()
    assert ui._stream_tail_fragments() == []
    assert "alpha" not in ui._transcript.render(40)
    assert "alpha" not in "".join(part[1] for part in ui._history_view.text())
