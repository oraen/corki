"""A streaming optimization must preserve lexical state across source lines."""

from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from corki.cli.markdown import AssistantBlock
from corki.cli.stream_markdown import StreamMarkdown


@pytest.mark.parametrize(
    "language, opening, closing",
    [
        ("python", 'value = """first', '"""'),
        ("rust", "/* first", "*/"),
        ("javascript", "const value = `first", "`"),
    ],
)
def test_multiline_code_style_matches_full_fence(language, opening, closing):
    output = StringIO()
    console = Console(
        file=output, width=80, force_terminal=True, color_system="truecolor", no_color=False
    )
    stream = StreamMarkdown()
    source = f"```{language}\n{opening}\n"
    stream.write(console, source)
    source += "second\n"
    stream.write(console, source)
    live = Text.from_ansi(output.getvalue())
    assert live.plain.count("second") == 1

    def canonical(text):
        rendered = StringIO()
        Console(
            file=rendered, width=80, force_terminal=True, color_system="truecolor", no_color=False
        ).print(AssistantBlock(text))
        return Text.from_ansi(rendered.getvalue())

    expected = canonical(source)
    live_style = live.get_style_at_offset(console, live.plain.index("second"))
    assert live_style == expected.get_style_at_offset(console, expected.plain.index("second"))

    # Negative control: highlighting each new line in isolation loses state.
    isolated = canonical(f"```{language}\nsecond\n```\n")
    assert live_style != isolated.get_style_at_offset(console, isolated.plain.index("second"))

    source += closing + "\n```\n"
    stream.write(console, source)
    assert Text.from_ansi(output.getvalue()).plain.count("second") == 1
