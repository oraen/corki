import pytest
from prompt_toolkit import ANSI
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines
from rich.console import Console

from corki.cli.history_rows import HistoryRows


def compact(row):
    result = []
    for style, text in row:
        if not text:
            continue
        if result and result[-1][0] == style:
            result[-1] = style, result[-1][1] + text
        else:
            result.append((style, text))
    return result


@pytest.mark.parametrize("chunk", [1, 7, 1000])
def test_streamed_ansi_matches_existing_parser_across_writes_and_lines(chunk):
    text = "\x1b[31m中\t文\n仍然红色\x1b[0m\n\x1b[1;38;2;1;2;3m尾\x1b[0m\n"
    expected = [compact(row) for row in split_lines(to_formatted_text(ANSI(text)))]
    with HistoryRows() as rows:
        for start in range(0, len(text), chunk):
            rows.write(text[start : start + chunk])
        rows.finish()
        assert list(rows) == expected


def test_large_hidden_history_retains_only_bounded_visible_rows():
    with HistoryRows(cache_bytes=4096) as rows:
        for index in range(10000):
            rows.write(f"row-{index:05} 中文\n")
        rows.finish()
        assert len(rows) == 10001 and not rows._cache
        for index in range(10000):
            assert rows[index][0][1] == f"row-{index:05} 中文"
            assert len(rows._cache) <= 128 and rows._cached_bytes <= 4096
        assert rows[0][0][1] == "row-00000 中文"
        assert rows[-1] == []
    assert rows.closed and rows._data.closed and rows._index.closed
    assert not rows._cache


def test_rich_console_writes_to_sink_without_full_render_string(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    with HistoryRows() as rows:
        console = Console(
            file=rows, width=20, force_terminal=True, color_system="standard", no_color=False
        )
        console.print("中文 " * 30, style="red")
        rows.finish()
        assert len(rows) > 3
        assert all("ansired" in style for row in rows for style, text in row if text.strip())


@pytest.mark.parametrize("kind", ["capacity", "row"])
def test_capacity_failure_does_not_leak_temp_files(kind):
    rows = HistoryRows(max_bytes=100 if kind == "capacity" else 1000000)
    with pytest.raises(ValueError, match="limit"), rows:
        rows.write("x" * (65537 if kind == "row" else 200) + "\n")
    assert rows.closed and rows._data.closed and rows._index.closed
    rows.close()


def test_finish_empty_and_invalid_access():
    with HistoryRows() as rows:
        with pytest.raises(ValueError, match="not finished"):
            rows[0]
        rows.finish().finish()
        assert len(rows) == 1 and rows[0] == []
        with pytest.raises(IndexError):
            rows[1]
        with pytest.raises(ValueError, match="not writable"):
            rows.write("late")
    with pytest.raises(ValueError, match="closed"):
        rows[0]


def test_transcript_streaming_sink_preserves_full_tool_output_and_live_state(tmp_path):
    from io import StringIO

    from corki.cli.terminal import TerminalUI
    from corki.config import CorkiSettings

    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO()))
    ui.show_tool_output("START\n" + "中间行\n" * 1000 + "END")
    source = tuple(ui._transcript.calls)
    expected = ui._transcript.render(40, expand_tools=True)
    with HistoryRows() as rows:
        assert ui._transcript.render(40, expand_tools=True, output=rows) is None
        rows.finish()
        actual = "\n".join("".join(text for _, text in row) for row in rows)
        assert actual == expected
        assert actual.count("中间行") == 1000 and "START" in actual and "END" in actual
    assert tuple(ui._transcript.calls) == source
    assert not ui._transcript.replaying and not ui._transcript.expand_tools
    console = ui._console
    with pytest.raises(ValueError, match="limit"), HistoryRows(max_bytes=100) as limited:
        ui._transcript.render(40, expand_tools=True, output=limited)
        limited.finish()
    assert limited.closed
    assert ui._console is console and tuple(ui._transcript.calls) == source
    assert not ui._transcript.replaying and not ui._transcript.expand_tools
