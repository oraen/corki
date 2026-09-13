"""Completed, cold and resized assistant messages share Markdown display source."""

import asyncio
from io import StringIO
from pathlib import Path

import pytest
from rich.color import ColorType
from rich.console import Console

from corki.cli.markdown import AssistantMarkdown
from corki.cli.terminal import TerminalUI
from corki.cli.transcript import Transcript
from corki.config import CorkiSettings


def test_stream_commits_newlines_and_discards_interrupted_tail(tmp_path):
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=output, width=100, color_system=None),
    )
    ui.begin_assistant_message()
    ui.append_assistant_delta("complete")
    assert output.getvalue() == ""
    ui.append_assistant_delta(" line\nincomplete")
    assert "complete line" in output.getvalue() and "incomplete" not in output.getvalue()
    before = output.getvalue()
    assert "incomplete" not in ui._transcript.render(40)
    assert (ui._assistant_pending, ui._assistant_started) == ("incomplete", True)
    ui.append_assistant_delta(" tail")
    assert output.getvalue() == before
    ui.end_assistant_message()
    assert "incomplete" not in output.getvalue()
    assert "incomplete" not in ui._transcript.render(100)
    ui.begin_assistant_message()
    ui.append_assistant_delta("final answer")
    asyncio.run(ui.complete_assistant_message("final answer"))
    assert output.getvalue().count("final answer") == 1
    assert "incomplete" not in output.getvalue()


SOURCE = """# Result

Use **bold**, *italic*, and `x=1`.

- First item with enough words to wrap around the narrow terminal.
- Second item

1. Ordered item

> Quoted text

```python
  print("[literal] **code**")  
```

[Guide](https://example.test/guide)"""


@pytest.mark.parametrize("width", [40, 100])
def test_markdown_display_and_source_reflow_snapshot(width):
    ui = TerminalUI.__new__(TerminalUI)
    output = StringIO()
    ui._console = Console(file=output, width=width, color_system=None)
    ui._transcript = Transcript(ui)
    ui.show_assistant_message(SOURCE)
    rendered = output.getvalue()
    assert rendered == ui._transcript.render(width)
    assert len(ui._transcript.calls) == 1
    assert ui._transcript.calls[0][1] == (SOURCE,)
    normalized = "\n".join(line.rstrip() for line in rendered.strip("\n").splitlines()) + "\n"
    expected = Path(__file__).with_name("snapshots") / f"assistant_markdown_{width}.txt"
    assert normalized == expected.read_text()
    assert 'print("[literal] **code**")  \n' in rendered
    assert all(not line.strip() or line.startswith(("• ", "  ")) for line in rendered.splitlines())


@pytest.mark.parametrize(
    "source, repair",
    [
        ("plain", False),
        ("**bold**", True),
        ("`code`", True),
        ("a\nb", True),
        (r"\*literal\*", True),
        ("A &amp; B", True),
    ],
)
def test_markdown_stream_repair_requirement(source, repair):
    assert AssistantMarkdown(source).needs_stream_repair is repair


@pytest.mark.parametrize("source", ["First <sup>value</sup>\nSecond", "<div>literal</div>"])
def test_markdown_preserves_html_and_source_linebreaks(source):
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)
    console.print(AssistantMarkdown(source))
    assert [line.rstrip() for line in output.getvalue().splitlines()] == source.splitlines()


@pytest.mark.parametrize(
    "source,width,expected",
    [
        ("0. Zero\n1. One", 40, ["0. Zero", "1. One"]),
        (
            "- outer item with several words to wrap\n  - inner item that also needs wrapping",
            20,
            [
                "- outer item with",
                "  several words to",
                "  wrap",
                "    - inner item",
                "      that also",
                "      needs wrapping",
            ],
        ),
        (
            "1. First:\n\n   ```rust\n   fn first() {}\n   ```\n\n2. Second:",
            40,
            ["1. First:", "", "   fn first() {}", "", "2. Second:"],
        ),
        (
            "1. outer\n   - inner\n     - deeper\n2. next",
            40,
            ["1. outer", "    - inner", "        - deeper", "", "2. next"],
        ),
    ],
)
def test_native_list_layout_samples(source, width, expected):
    output = StringIO()
    Console(file=output, width=width, color_system=None).print(AssistantMarkdown(source))
    assert [line.rstrip() for line in output.getvalue().splitlines()] == expected


def test_initial_code_blank_line_is_not_a_container_separator():
    output = StringIO()
    Console(file=output, width=40, color_system=None).print(
        AssistantMarkdown("```text\n\n  code  \n```")
    )
    assert output.getvalue() == "\n  code  \n"


@pytest.mark.parametrize("info", ["python", "python,no_run", "python title=demo", "python\tno_run"])
def test_code_language_metadata_highlights_without_changing_source(info):
    console = Console(width=100, color_system="truecolor")
    segments = list(console.render(AssistantMarkdown(f"```{info}\n\n  print('literal')  \n```")))
    assert "".join(segment.text for segment in segments) == "\n  print('literal')  \n"
    assert any(
        segment.style and segment.style.color and segment.style.color.type == ColorType.TRUECOLOR
        for segment in segments
    )
    assert all(not segment.style or segment.style.bgcolor is None for segment in segments)


@pytest.mark.parametrize(
    "source",
    ["x" * 4097, "界" * 1366, "x\n" * 10001, ("x" * 100 + "\n") * 5200],
    ids=["long_line", "wide_line", "many_lines", "large_block"],
)
def test_code_highlighting_limits_skip_lexer(source, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("oversized code must not enter the lexer")

    monkeypatch.setattr("corki.cli.syntax.get_lexer_by_name", unexpected)
    console = Console(width=120, color_system="truecolor")
    segments = list(console.render(AssistantMarkdown(f"```python\n{source}\n```")))
    assert segments
    assert all(not segment.style or segment.style.color is None for segment in segments)


def test_unknown_language_and_crlf_preserve_literal_code():
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)
    console.print(AssistantMarkdown("```unknown-corki-language\r\n  **literal**  \r\nnext\r\n```"))
    assert output.getvalue() == "  **literal**  \nnext\n"


@pytest.mark.parametrize("width", [40, 100])
def test_highlighted_code_display_matches_transcript_replay(width):
    ui = TerminalUI.__new__(TerminalUI)
    output = StringIO()
    ui._console = Console(file=output, width=width, color_system="truecolor", force_terminal=True)
    ui._transcript = Transcript(ui)
    source = "```python,no_run\nprint('literal')\n```"
    ui.show_assistant_message(source)
    assert output.getvalue() == ui._transcript.render(width)
    assert "\x1b[" in output.getvalue()
    assert ui._transcript.calls[0][1] == (source,)


@pytest.mark.parametrize("language", ["", "python", "unknown-corki-language"])
def test_code_logical_line_does_not_wrap(language):
    code = 'fn main() { println!("hi from a long line"); }'
    console = Console(width=10, color_system=None)
    rendered = "".join(
        s.text for s in console.render(AssistantMarkdown(f"```{language}\n{code}\n```"))
    )
    assert rendered == code + "\n"


@pytest.mark.parametrize("width", [40, 100])
def test_terminal_code_continuations_keep_source_indentation(width):
    ui = TerminalUI.__new__(TerminalUI)
    output = StringIO()
    ui._console = Console(file=output, width=width, color_system=None)
    ui._transcript = Transcript(ui)
    code = "    " + " ".join(f"word{n}" for n in range(30))
    source = f"Intro\n\n```text\n{code}\n```"
    ui.show_assistant_message(source)
    rendered = output.getvalue()
    assert rendered == ui._transcript.render(width)
    code_lines = [line for line in rendered.splitlines() if "word" in line]
    assert len(code_lines) > 1
    assert all(line.startswith("      ") for line in code_lines)
    assert " ".join(line.strip() for line in code_lines) == code.strip()
    assert all(len(line) <= width for line in code_lines)


@pytest.mark.parametrize(
    "source,prefix",
    [
        ("- outer\n  - inner\n\n    ```\n    LONG_CODE\n    ```", "      "),
        ("> ```\n> LONG_CODE\n> ```", "> "),
    ],
)
def test_nested_code_retains_complete_logical_line(source, prefix):
    code = "a_long_code_identifier_" * 5
    console = Console(width=20, color_system=None)
    rendered = "".join(
        s.text for s in console.render(AssistantMarkdown(source.replace("LONG_CODE", code)))
    )
    assert prefix + code + "\n" in rendered
