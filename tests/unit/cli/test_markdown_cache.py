from io import StringIO

import pytest
from rich.console import Console
from rich.segment import Segment
from rich.theme import Theme

from corki.cli.markdown import AssistantMarkdown
from corki.cli.markdown_cache import MarkdownParseCache
from corki.cli.markdown_render_cache import MarkdownRenderCache
from corki.cli.stream_markdown import StreamMarkdown, _render_lines


def test_tail_cache_matches_full_render_after_rewrites_and_resize():
    stream = StreamMarkdown()
    for prefix, tail in (
        ("Before\n\n", "| A | B |\n| --- | --- |\n| one | two |\n"),
        ("Before\n\n```md\n", "| A | B |\n| --- | --- |\n| one | two |\n```\n"),
        ("[late][ref]\n\n", "| A | B |\n| --- | --- |\n\n[ref]: /destination\n"),
        ("different same stream\n\n", "> | A | B |\n> | --- | --- |\n> | one | two |\n"),
    ):
        source = prefix
        for line in tail.splitlines(keepends=True):
            source += line
            for width in (40, 100, 40):
                console = Console(file=StringIO(), width=width)
                expected = _render_lines(console, source)[len(_render_lines(console, prefix)) :]
                assert stream.tail_lines(console, source, len(prefix)) == expected


@pytest.mark.parametrize(
    "source",
    [
        "# Title\n\nfirst **bold**\ncontinued\n\nnext `code`\n",
        "Intro\n\n```python\nprint('a')\n\nprint('b')\n```\n\nafter\n",
        "Title\n===\n\n- one\n- two\n\n  continued\n\nafter\n",
        "> quote\n>\n> second\n\noutside\n",
        "| A | B |\n| --- | --- |\n| one | two |\n\nafter\n",
        "[late][ref]\n\nother\n\n[ref]: /path\n\n[late][ref]\n",
        "[name]\n\n[name]:\n  /path\n  'title'\n\nother\n",
        "Unicode 中文\n\n    literal\n    second\n\noutside\n",
        "first\r\n\r\nsecond\r\n",
        "one\vstill\n\nsecond\n\nthird\n",
        "before\n\n---\n\n- a\n- b\n\n> quoted\n\nend\n",
        "before\n\n````python\na = 1\n```\nb = 2\n````\n\nafter\n",
        "~~~python extra\na = 1\n```\n  ~~~ trailing\nb = 2\n~~~\n",
        " ```python\n  a = 1\n  b = 2\n ```\n",
        "```py\\thon\na = 1\nb = 2\n```\n",
        "```py&amp;thon\na = 1\nb = 2\n```\n",
        "```\na = 1\nb = 2\n```\n",
        "```python\na = '\0'\nb = 2\n```\n",
        "before 中文\n\n```python\na = '中文'\nb = 2\n```\n",
    ],
)
def test_cached_parse_matches_full_parse_at_every_line(source):
    cache, accumulated = MarkdownParseCache(), ""
    cache.markdown._block_cache = MarkdownRenderCache()
    for line in source.splitlines(keepends=True):
        accumulated += line
        actual = cache.document(accumulated)
        assert [t.as_dict() for t in actual.parsed] == [
            t.as_dict() for t in AssistantMarkdown(accumulated).parsed
        ]
        for width in (40, 100):
            console = Console(file=StringIO(), width=width)
            assert list(Segment.simplify(console.render(actual))) == list(
                Segment.simplify(console.render(AssistantMarkdown(accumulated)))
            )


def test_finished_paragraphs_are_not_reparsed(monkeypatch):
    stream = StreamMarkdown()
    cache = stream.parse_cache
    output = StringIO()
    console = Console(file=output, width=80)
    parse = cache.parser.parse
    lengths = []

    def counted(source, *args, **kwargs):
        lengths.append(len(source))
        return parse(source, *args, **kwargs)

    monkeypatch.setattr(cache.parser, "parse", counted)
    source = ""
    full_parse_length = 0
    for index in range(100):
        source += f"paragraph {index}\n\n"
        full_parse_length += len(source)
        stream.write(console, source)
    assert sum(lengths) < len(source) * 3
    assert full_parse_length > len(source) * 40
    assert output.getvalue().count("paragraph") == 100
    before = len(lengths)
    cache.document(source)
    assert len(lengths) == before
    assert cache.document("replacement\n").parsed == AssistantMarkdown("replacement\n").parsed


def test_finished_paragraphs_are_not_rerendered(monkeypatch):
    from rich.markdown import Paragraph

    calls = 0
    render = Paragraph.__rich_console__

    def counted(self, console, options):
        nonlocal calls
        calls += 1
        yield from render(self, console, options)

    monkeypatch.setattr(Paragraph, "__rich_console__", counted)
    stream = StreamMarkdown()
    output = StringIO()
    console = Console(file=output, width=80)
    source = ""
    for index in range(100):
        source += f"paragraph {index}\n\n"
        stream.write(console, source)
    assert calls < 250
    assert output.getvalue().count("paragraph") == 100


def test_render_cache_invalidates_changed_theme_without_source_change():
    stream = StreamMarkdown()
    document = stream.parse_cache.document("**strong**\n\nsecond\n")
    console = Console(file=StringIO(), width=40)
    original = list(Segment.simplify(console.render(document)))
    with console.use_theme(Theme({"markdown.strong": "bold red"})):
        changed = list(Segment.simplify(console.render(document)))
        assert changed != original
        assert changed == list(Segment.simplify(console.render(AssistantMarkdown(document.markup))))
    assert list(Segment.simplify(console.render(document))) == original


def test_open_code_does_not_reparse_entire_body(monkeypatch):
    stream = StreamMarkdown()
    cache = stream.parse_cache
    output = StringIO()
    console = Console(file=output, width=80)
    original = cache.parser.parse
    lengths = []

    def counted(source, *args, **kwargs):
        lengths.append(len(source))
        return original(source, *args, **kwargs)

    monkeypatch.setattr(cache.parser, "parse", counted)
    source = "```python\n"
    stream.write(console, source)
    for index in range(100):
        source += f"value_{index} = {index}\n"
        stream.write(console, source)
        document = cache.markdown
        assert document.parsed == AssistantMarkdown(source).parsed
    assert sum(lengths) < len(source) * 3
    assert output.getvalue().count("value_") == 100
    source += "```\n\nafter\n"
    stream.write(console, source)
    assert cache.markdown.parsed == AssistantMarkdown(source).parsed
    assert output.getvalue().count("value_") == 100
