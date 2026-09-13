from io import StringIO

import pytest
from rich.console import Console

from corki.cli.markdown import AssistantMarkdown
from corki.cli.markdown_cache import MarkdownParseCache

TABLE = "| A | B |\n|---|---|\n| one | two |\n"


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("opening,closing", [("```md", "```"), ("~~~Markdown extra", "~~~~")])
def test_closed_markdown_table_renders_like_bare_table(width, opening, closing):
    source = opening + "\n" + TABLE + closing + "\n"
    output = []
    for body in (source, TABLE):
        stream = StringIO()
        Console(file=stream, width=width).print(AssistantMarkdown(body))
        output.append(stream.getvalue())
    assert output[0] == output[1]
    assert "one" in output[0]


@pytest.mark.parametrize(
    "source,expected",
    [
        ("```md\n" + TABLE + "```\n", TABLE),
        ("```md\n" + TABLE, "```md\n" + TABLE),
        ("```python\n" + TABLE + "```\n", None),
        ("```md,extra\n" + TABLE + "```\n", None),
        ("```markdown\n**bold**\n```\n", None),
        ("```md\n| A | B |\n\n|---|---|\n```\n", None),
        ("```md\n|---|---|\n|---|---|\n```\n", None),
        ("```md\n> | A | B |\n> |---|---|\n```\n", None),
        ("> ```md\n> | Only |\n> |---|\n> ```\n", "> | Only |\n> |---|\n"),
        ("> ```md\n> | Only |\n> |---|\n```\n", None),
        ("````md\n" + TABLE + "```\n", None),
        ("```md\n" + TABLE + "``` trailing\n", None),
    ],
)
def test_display_normalization_and_incremental_cache(source, expected):
    expected = source if expected is None else expected
    assert AssistantMarkdown(source).markup == expected
    cache = MarkdownParseCache()
    prefix = ""
    for line in source.splitlines(keepends=True):
        prefix += line
        assert [t.as_dict() for t in cache.document(prefix).parsed] == [
            t.as_dict() for t in AssistantMarkdown(prefix).parsed
        ]
    assert cache.document(source).markup == expected
