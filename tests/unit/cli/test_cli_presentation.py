"""Presentation checks keep original evidence independent of compact CLI rows."""

from io import StringIO

import pytest
from prompt_toolkit.formatted_text import fragment_list_to_text
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from corki.cli.message_layout import user_message_rows
from corki.cli.terminal import TerminalUI
from corki.cli.tool_preview import command_title, compact_rows, output_rows
from corki.cli.transcript import Transcript
from corki.cli.working_status import WorkingStatus


@pytest.mark.parametrize("width", [5, 20, 40, 100])
def test_commands_show_shell_content_not_wire_json_and_disclose_original(width):
    raw = '{"cmd":"python -c \\"print(123)\\"\\necho done","yield_time_ms":1000}'
    title = command_title("exec_command", raw, width)
    assert title is not None and cell_len(title.plain) <= width
    if width >= 40:
        assert "python" in title.plain and "yield_time_ms" not in title.plain
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=StringIO(), width=width, color_system=None)
    ui._transcript = Transcript(ui)
    ui.show_tool_started("exec_command", raw)
    assert ui._transcript.calls[-1][1] == ("exec_command", raw)
    assert "yield_time_ms" in ui._transcript.render(100, expand_tools=True)


@pytest.mark.parametrize("raw", ["{", "null", "[]", '{"cmd":42}', '{"cmd":""}', "x" * 100001])
def test_invalid_command_preview_uses_existing_literal_fallback(raw):
    assert command_title("exec_command", raw, 80) is None


def test_terminal_control_sequences_never_execute_in_command_title():
    title = command_title("exec_command", '{"cmd":"echo \\u001b[2J"}', 80)
    assert "\x1b" not in title.plain and r"\x1b[2J" in title.plain


@pytest.mark.parametrize("width", [5, 12, 40, 100])
def test_user_history_has_consistent_gutter_and_explicit_newlines(width):
    console = Console(file=StringIO(), width=width)
    rows = list(user_message_rows(console, "中文 hello\nsecond line " * 5))
    assert rows[0].plain.startswith("› ")
    assert all(row.plain.startswith("  ") for row in rows[1:])
    assert all(cell_len(row.plain) <= width for row in rows)
    assert "\x1b" not in "".join(row.plain for row in user_message_rows(console, "\x1b[2J"))


@pytest.mark.parametrize("width", [5, 20, 80])
def test_bounded_rows_match_rich_wrapping_including_tabs_and_blank_lines(width):
    console = Console(file=StringIO(), width=width)
    source = "a\n\n中文 line\n\ta\t\nlast   \n"
    expected = Text(source.removesuffix("\n")).wrap(console, max(1, width - 4))
    assert [r.plain for r in output_rows(console, source)] == [r.plain for r in expected]


def test_preview_keeps_five_rows_and_counts_omissions_without_collecting_all():
    seen = []

    def rows():
        for i in range(10000):
            seen.append(i)
            yield Text(str(i))

    result = [r.plain for r in compact_rows(rows())]
    assert result == ["0", "1", "… +9996 lines (ctrl+t to expand)", "9998", "9999"]
    assert len(seen) == 10000


def test_status_details_wrap_at_three_rows_and_reuse_measured_layout():
    status = WorkingStatus(lambda: None, animated=False)
    detail = "Reading local files with a longer explanation and more detail " * 1000
    text = fragment_list_to_text(status.fragments(30, detail=detail))
    assert len(text.splitlines()) == 4
    assert text.endswith("…")
    assert all(cell_len(row) <= 30 for row in text.splitlines())
    cache = status._details_cache
    status.fragments(30, detail=detail)
    assert status._details_cache is cache
    status.fragments(80, detail="Short")
    assert status._details_cache is not cache
    assert len(fragment_list_to_text(status.fragments(80, detail="Short")).splitlines()) == 2


@pytest.mark.parametrize("light,theme", [(True, "default"), (False, "monokai")])
def test_command_highlighting_uses_existing_terminal_palette(monkeypatch, light, theme):
    from corki.cli import tool_preview

    used = []

    def highlight(code, language, *, theme):
        used.append((language, theme))
        return Text(code)

    monkeypatch.setattr(tool_preview, "highlight_code", highlight)
    title = command_title("exec_command", '{"cmd":"python -V"}', 80, light=light)
    assert used == [("shell", theme)]
    assert title.plain == "• python -V"


def test_generic_header_marks_screen_row_truncation_once():
    ui = TerminalUI.__new__(TerminalUI)
    out = StringIO()
    ui._console = Console(file=out, width=20, color_system=None)
    ui._transcript = Transcript(ui)
    ui.show_tool_started("fixture_tool", "中" * 100)
    rows = out.getvalue().splitlines()
    assert len(rows) == 2 and rows[-1].endswith("…") and not rows[-1].endswith("……")
    assert all(cell_len(row) <= 20 for row in rows)
