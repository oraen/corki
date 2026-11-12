"""Explicit user-message wrapping, matching the assistant's two-column gutter."""

from rich.text import Text

from corki.cli.display_text import visible_terminal_text
from corki.cli.theme import ACCENT


def user_message_rows(console, message):
    # Codex UserHistoryCell wraps before adding its gutter and right margin.
    # Explicit continuation indentation survives transcript replay and resize.
    width = max(1, console.width)
    prefix = "› " if width >= 3 else ""
    body = Text(visible_terminal_text(message))
    for index, row in enumerate(body.wrap(console, max(1, width - len(prefix) - 1))):
        yield Text.assemble((prefix if index == 0 else " " * len(prefix), f"{ACCENT} bold"), row)
