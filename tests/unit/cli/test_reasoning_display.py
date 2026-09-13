from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ReasoningItem, new_step_id
from corki.protocol.response_body import capture_response_body


@pytest.mark.parametrize(
    "text,header",
    [("plain text", None), ("**   ** **later**", None), ("**first** **second**", "first")],
)
def test_reasoning_header_matches_first_bold_contract(tmp_path, text, header):
    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO()))
    ui.begin_reasoning()
    for char in text:
        ui.append_reasoning_delta(char)
    assert ui._reasoning_header == header
    assert (header or "Thinking") in ui._toolbar()[0][1]
    ui.end_reasoning()
    assert not ui._reasoning_active


def test_reasoning_status_and_detail_replay_are_separate(tmp_path):
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=output, width=80)
    )
    ui.begin_reasoning()
    ui.append_reasoning_delta("**Inspect")
    assert ui._reasoning_header is None
    ui.append_reasoning_delta("ing files** PRIVATE_BODY")
    assert ui._reasoning_header == "Inspecting files"
    assert output.getvalue() == ""
    for width in (40, 100):
        assert "PRIVATE_BODY" not in ui._transcript.render(width)
        assert "PRIVATE_BODY" in ui._transcript.render(width, include_reasoning=True)
        assert ui._reasoning_header == "Inspecting files"
    ui.end_reasoning()
    assert ui._reasoning_header is None
    assert ui._reasoning_buffer == ""
    assert "PRIVATE_BODY" in ui._transcript.render(80, include_reasoning=True)
    assert output.getvalue() == ""


def test_cold_reasoning_history_retains_summary_without_printing_it(tmp_path):
    output = StringIO()
    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=output))
    item = ReasoningItem(
        "opaque", new_turn_id(), new_step_id(), summary="**Review** retained detail"
    )
    ui.replay_history((item,))
    assert output.getvalue() == ""
    assert not ui._reasoning_active
    assert "retained detail" not in ui._transcript.render(80)
    detailed = ui._transcript.render(80, include_reasoning=True)
    assert "retained detail" in detailed and "opaque" not in detailed


@pytest.mark.parametrize("summary", [None, "", "Explicit summary"])
def test_legacy_body_fallback_only_for_absent_summary(tmp_path, summary):
    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO()))
    item = ReasoningItem(
        "RAW_SECRET",
        new_turn_id(),
        new_step_id(),
        summary=summary,
        response_body_json=capture_response_body(
            {
                "type": "reasoning",
                "summary": [
                    {"type": "summary_text", "text": "First section"},
                    {"type": "summary_text", "text": "Second section"},
                ],
            }
        ),
    )
    ui.replay_history((item,))
    detailed = ui._transcript.render(80, include_reasoning=True)
    assert "RAW_SECRET" not in detailed
    assert ("First section" in detailed) == (summary is None)
    assert ("Second section" in detailed) == (summary is None)
    assert ("Explicit summary" in detailed) == bool(summary)
    assert item.summary is summary


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize("width", [40, 100])
def test_detailed_reasoning_renders_markdown_across_fragments(tmp_path, finished, width):
    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO()))
    ui.begin_reasoning()
    ui.append_reasoning_delta("**Review")
    ui.show_notice("interleaved notice")
    ui.append_reasoning_delta("** using `code`\n\n- first\n- second\n")
    if finished:
        ui.end_reasoning()
    source = list(ui._transcript.calls)
    detailed = ui._transcript.render(width, include_reasoning=True)
    assert "**Review" not in detailed and "`code`" not in detailed
    assert "Review using code" in detailed
    assert detailed.index("Review") < detailed.index("interleaved notice")
    assert "first" in detailed and "second" in detailed
    assert [line.rstrip() for line in detailed.splitlines() if line.strip()] == [
        "• Review using code",
        "  - first",
        "  - second",
        "interleaved notice",
    ]
    assert ui._transcript.calls == source
    assert ui._reasoning_active == (not finished)


def test_reasoning_markdown_keeps_dim_italic_and_bold(tmp_path):
    console = Console(file=StringIO(), force_terminal=True, no_color=False, width=80)
    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history", console=console)
    ui.begin_reasoning()
    ui.append_reasoning_delta("**Review** body")
    ui.end_reasoning()
    text = Text.from_ansi(ui._transcript.render(80, include_reasoning=True))
    title = text.get_style_at_offset(console, text.plain.index("Review"))
    body = text.get_style_at_offset(console, text.plain.index("body"))
    assert title.bold and title.dim and title.italic
    assert body.dim and body.italic
