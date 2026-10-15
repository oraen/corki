"""Bounded foreground-only syntax spans which cannot rewrite display text."""

from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.lexers.special import TextLexer
from rich.style import Style
from rich.syntax import Syntax


def highlight_code(code, language=None, *, filename=None, theme="monokai"):
    lines = code.split("\n")
    if lines[-1] == "":
        lines.pop()
    if (
        not code
        or len(code.encode("utf-8")) > 512 * 1024
        or len(lines) > 10_000
        or any(len(line.encode("utf-8")) > 4096 for line in lines)
    ):
        return None
    try:
        lexer = (
            get_lexer_for_filename(filename, stripnl=False, ensurenl=False)
            if filename is not None
            else get_lexer_by_name(language or "", stripnl=False, ensurenl=False)
        )
        if isinstance(lexer, TextLexer):
            return None
        highlighted = Syntax(code, lexer, theme=theme, word_wrap=True).highlight(code)
    except Exception:
        # Syntax decoration must not hide the operation awaiting approval.
        return None
    if highlighted.plain != code:
        return None
    highlighted.style = ""
    highlighted.justify = None
    highlighted.no_wrap = True
    highlighted.overflow = "ignore"
    highlighted.spans = [
        type(span)(
            span.start,
            span.end,
            Style(
                color=span.style.color,
                bold=span.style.bold,
                italic=span.style.italic,
                underline=span.style.underline,
            ),
        )
        for span in highlighted.spans
    ]
    return highlighted
