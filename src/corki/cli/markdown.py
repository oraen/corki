"""Markdown display source with the CLI's two-column assistant gutter."""

import re
from copy import copy

from rich.console import Group
from rich.markdown import BlockQuote, CodeBlock, Heading, ListElement, ListItem, Markdown, Paragraph
from rich.segment import Segment
from rich.text import Text

from corki.cli.display_text import visible_terminal_text
from corki.cli.markdown_fences import unwrap_markdown_fences
from corki.cli.syntax import highlight_code


def _prefixed(console, options, renderable, first, following):
    lines = Segment.split_lines(
        console.render(renderable, options.update(width=max(1, options.max_width - len(first))))
    )
    for index, line in enumerate(lines):
        yield Segment(first if index == 0 else following)
        yield from line
        yield Segment.line()


class _Indent:
    def __init__(self, body, width):
        self.body, self.prefix = body, " " * width

    def __rich_console__(self, console, options):
        yield from _prefixed(console, options, self.body, self.prefix, self.prefix)


class _Heading(Heading):
    def __rich_console__(self, console, options):
        yield Text.assemble(("#" * int(self.tag[1:]) + " ", "bold"), self.text)


class _CodeBlock(CodeBlock):
    @classmethod
    def create(cls, markdown, token):
        result = super().create(markdown, token)
        result.indented = token.type == "code_block"
        result.lexer_name = re.split(r"[, \t]", token.info or "", maxsplit=1)[0]
        return result

    def __rich_console__(self, console, options):
        # Do not strip indentation/trailing spaces or interpret markup inside code.
        code = self.text.plain.removesuffix("\n")
        if self.indented:
            code = "\n".join("    " + line for line in code.split("\n"))
        plain = Text(code, no_wrap=True, overflow="ignore")
        highlighted = highlight_code(code, self.lexer_name, theme=self.theme)
        if highlighted is None:
            yield plain
            return
        yield highlighted


class _Quote(BlockQuote):
    def __rich_console__(self, console, options):
        yield from _prefixed(console, options, self.elements, "> ", "> ")


class _ListItem(ListItem):
    def _body(self, marker):
        children = []
        for index, child in enumerate(self.elements):
            if index and isinstance(child, (CodeBlock, Paragraph)):
                children.append(Text(""))
            if isinstance(child, ListElement):
                child = _Indent(child, max(0, 4 - len(marker)))
            children.append(child)
        return Group(*children)

    def render_bullet(self, console, options):
        yield from _prefixed(console, options, self._body("- "), "- ", "  ")

    def render_number(self, console, options, number, last_number):
        marker = f"{number}. "
        yield from _prefixed(console, options, self._body(marker), marker, " " * len(marker))


class _List(ListElement):
    def __rich_console__(self, console, options):
        separate = False
        start = 1 if self.list_start is None else self.list_start
        for index, item in enumerate(self.items):
            if separate:
                yield Segment.line()
            segments = tuple(
                item.render_bullet(console, options)
                if self.list_type == "bullet_list_open"
                else item.render_number(console, options, start + index, start + len(self.items))
            )
            separate = sum(segment.text.count("\n") for segment in segments) > 1
            yield from segments


class AssistantMarkdown(Markdown):
    elements = {
        **Markdown.elements,
        "heading_open": _Heading,
        "fence": _CodeBlock,
        "code_block": _CodeBlock,
        "html_block": _CodeBlock,
        "blockquote_open": _Quote,
        "list_item_open": _ListItem,
        "bullet_list_open": _List,
        "ordered_list_open": _List,
    }

    def __init__(self, source):
        # Keep link destinations visible in non-hyperlink terminals and exports.
        super().__init__(unwrap_markdown_fences(visible_terminal_text(source)), hyperlinks=False)

    def __rich_console__(self, console, options):
        # Rich's nested-element bookkeeping emits a separator before a root
        # container. It is not a source blank line (unlike blank lines in code).
        cache = getattr(self, "_block_cache", None)
        if cache is not None:
            yield from cache.render(self, console, options)
            return
        first = True
        container = bool(self.parsed) and self.parsed[0].type in {
            "bullet_list_open",
            "ordered_list_open",
            "blockquote_open",
            "table_open",
        }
        for segment in super().__rich_console__(console, options):
            if first and container and segment.text == "\n":
                first = False
                continue
            first = False
            yield segment

    def _flatten_tokens(self, tokens):
        for token in super()._flatten_tokens(tokens):
            if token.type in {"softbreak", "html_inline"}:
                token = copy(token)
                token.type = "hardbreak" if token.type == "softbreak" else "text"
            yield token

    @property
    def needs_stream_repair(self):
        tokens = tuple(self._flatten_tokens(self.parsed))
        return (
            any(token.type not in {"paragraph_open", "paragraph_close", "text"} for token in tokens)
            or "\n" in self.markup
            or "".join(token.content for token in tokens if token.type == "text") != self.markup
        )


class AssistantBlock:
    def __init__(self, source):
        self.markdown = (
            source if isinstance(source, AssistantMarkdown) else AssistantMarkdown(source)
        )

    def __rich_console__(self, console, options):
        lines = Segment.split_lines(
            console.render(self.markdown, options.update(width=max(1, options.max_width - 2)))
        )
        for index, line in enumerate(lines):
            if index == 0 or any(segment.text.strip() for segment in line):
                text = Text()
                text.append("• " if index == 0 else "  ", "dim")
                for segment in line:
                    text.append(segment.text, segment.style)
                if text.cell_len > options.max_width:
                    # Logical code lines remain intact above. Scrollback folding
                    # repeats the actual leading whitespace, not a synthetic gutter.
                    prefix_length = len(text.plain) - len(text.plain.lstrip())
                    prefix = text[:prefix_length]
                    if prefix.cell_len >= options.max_width:
                        prefix = Text("")
                        prefix_length = 0
                    body = text[prefix_length:]
                    for wrapped in body.wrap(console, max(1, options.max_width - prefix.cell_len)):
                        yield prefix + wrapped
                    continue
                yield from text.render(console, end="")
            yield Segment.line()
