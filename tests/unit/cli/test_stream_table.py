from io import StringIO

import pytest
from prompt_toolkit.data_structures import Size
from rich.console import Console
from rich.text import Text

from corki.cli.stream_table import TableStreamSource
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("intro", ["", "Before\n\n", "Before\n\n```md\nplain code\n"])
def test_live_tail_keeps_full_fence_context(tmp_path, monkeypatch, width, intro):
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=output, force_terminal=True, width=width),
    )
    monkeypatch.setattr(ui._session.app.output, "get_size", lambda: Size(rows=30, columns=width))
    ui.begin_assistant_message()
    opening = "" if intro.endswith("plain code\n") else "```md\n"
    ui.append_assistant_delta(intro + opening + "| A | B |\n| --- | --- |\n| one | two |\n")
    visible = "".join(text for _, text in ui._stream_tail_fragments())
    assert "| --- | --- |" in visible  # Still an open code fence, not a table.
    assert "Before" not in visible and "plain code" not in visible
    assert "  | one | two |" in visible
    ui.append_assistant_delta("```\n")
    visible = "".join(text for _, text in ui._stream_tail_fragments())
    assert "| --- | --- |" not in visible and "```" not in visible
    assert visible.count("one") == 1 and visible.count("two") == 1
    assert "Before" not in visible and "plain code" not in visible
    assert "one" not in Text.from_ansi(output.getvalue()).plain


def test_table_is_mutable_and_partial_row_is_not_visible(tmp_path):
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=output, force_terminal=True, width=40),
    )
    ui.begin_assistant_message()
    ui.append_assistant_delta("Before\n\n| Name | Value |\n")
    assert "Before" in output.getvalue()
    assert "Name" not in output.getvalue()
    ui.append_assistant_delta("| --- | --- |\n| alpha | one |\n| PARTIAL")
    visible = "".join(text for _, text in ui._stream_tail_fragments())
    assert "alpha" in visible and "PARTIAL" not in visible
    assert "alpha" not in output.getvalue()
    active = ui._table_source
    replay = ui._transcript.render(100)
    assert "alpha" not in replay
    assert ui._table_source is active
    assert "alpha" in "".join(text for _, text in ui._stream_tail_fragments())
    ui.end_assistant_message()
    assert ui._stream_tail_fragments() == []


def test_disabled_live_input_keeps_append_only_output(tmp_path):
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=output, force_terminal=True, width=40),
    )
    ui.set_live_input_enabled(False)
    ui.begin_assistant_message()
    ui.append_assistant_delta("| A | B |\n")
    assert "| A | B |" in output.getvalue()
    assert ui._stream_tail_fragments() == []


def test_pipe_prose_is_released_when_next_line_is_not_delimiter(tmp_path):
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=output, force_terminal=True, width=40),
    )
    ui.begin_assistant_message()
    ui.append_assistant_delta("a | b\n")
    assert "a | b" not in output.getvalue()
    ui.append_assistant_delta("ordinary next line\n")
    assert "a | b" in output.getvalue()
    assert ui._stream_tail_fragments() == []


@pytest.mark.parametrize(
    "prefix, held",
    [
        ("", True),
        ("```python\n", False),
        ("```md\n", True),
        ("~~~Markdown extra\n", True),
        ("```md,extra\n", False),
        ("````rust\n```\n", False),
        ("```rust\n~~~\n", False),
        ("```rust\n``` trailing\n", False),
        ("```rust\n```\n", True),
    ],
)
def test_table_fence_context(prefix, held):
    stream = TableStreamSource()
    emitted = stream.push(prefix)
    emitted += stream.push("| A | B |\n| --- | :---: |\n")
    assert bool(stream.tail) is held
    assert ("| A | B |" in emitted) is not held


def test_table_adjacency_escape_quote_and_sticky_holdback():
    stream = TableStreamSource()
    assert stream.push("A \\| B\n") == "A \\| B\n"
    assert stream.push("> | A | B |\n") == ""
    assert stream.push("\n") == ""
    assert stream.confirmed is None
    stream.push("ordinary\n")
    assert stream.tail == ""
    stream.push("> | A | B |\n> | --- | --- |\n")
    assert stream.confirmed is not None
    stream.push("\nordinary after table\n")
    assert "ordinary after table" in stream.tail


def test_tail_growth_reuses_stable_paragraph_rendering(tmp_path, monkeypatch):
    from markdown_it import MarkdownIt
    from rich.markdown import Paragraph

    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=StringIO(), force_terminal=True, width=80),
    )
    ui.begin_assistant_message()
    ui.append_assistant_delta("stable paragraph\n\n| A | B |\n| --- | --- |\n")
    calls = 0
    original = Paragraph.__rich_console__
    parse = MarkdownIt.parse
    prefix_parses = 0

    def counted_parse(self, source, *args, **kwargs):
        nonlocal prefix_parses
        prefix_parses += "stable paragraph" in source
        return parse(self, source, *args, **kwargs)

    def counted(self, console, options):
        nonlocal calls
        calls += 1
        yield from original(self, console, options)

    monkeypatch.setattr(Paragraph, "__rich_console__", counted)
    monkeypatch.setattr(MarkdownIt, "parse", counted_parse)
    for index in range(30):
        ui.append_assistant_delta(f"| row{index} | value |\n")
        visible = "".join(text for _, text in ui._stream_tail_fragments())
        assert visible.count(f"row{index} ") == 1
        assert "stable paragraph" not in visible
    assert calls <= 3
    assert prefix_parses <= 3
