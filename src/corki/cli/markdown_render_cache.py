"""Per-stream top-level Markdown body render cache, before assistant gutters."""

from copy import copy

from rich.segment import Segment

_CONTAINERS = {"bullet_list_open", "ordered_list_open", "blockquote_open", "table_open"}
_STYLES = (
    "markdown.paragraph",
    "markdown.block_quote",
    "markdown.code_block",
    "markdown.hr",
    "markdown.item",
    "markdown.item.bullet",
    "markdown.item.number",
    "markdown.link",
    "markdown.link_url",
    "markdown.table.border",
    "markdown.table.header",
)


class MarkdownRenderCache:
    def __init__(self):
        self.context = None
        self.blocks = {}

    def render(self, document, console, options):
        styles = (*_STYLES, *(f"markdown.{tag}" for tag in document.inlines))
        context = (
            options.max_width,
            console.color_system,
            options.ascii_only,
            document.code_theme,
            document.inline_code_lexer,
            document.inline_code_theme,
            document.justify,
            document.hyperlinks,
            console.get_style(document.style),
            tuple(console.get_style(name, default="none") for name in styles),
        )
        old = self.blocks if context == self.context else {}
        current = {}
        starts = [
            i for i, token in enumerate(document.parsed) if token.level == 0 and token.nesting >= 0
        ]
        previous_newline = False
        for number, start in enumerate(starts):
            end = starts[number + 1] if number + 1 < len(starts) else len(document.parsed)
            first = document.parsed[start]
            key = id(first)
            cached = old.get(key)
            if cached is None or cached[0] is not first:
                fragment = copy(document)
                fragment.parsed = document.parsed[start:end]
                fragment._block_cache = None
                segments = tuple(console.render(fragment, options))
                cached = (first, segments)
            current[key] = cached
            # Rich containers set new_line while closing their children, even
            # when the preceding root (e.g. a horizontal rule) did not request it.
            if number and (previous_newline or first.type in _CONTAINERS):
                yield Segment.line()
            yield from cached[1]
            element = document.elements.get(first.type)
            previous_newline = bool(element and element.new_line)
        self.context = context
        # Keep only this snapshot's blocks; do not accumulate discarded tails.
        self.blocks = current
