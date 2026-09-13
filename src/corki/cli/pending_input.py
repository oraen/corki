"""Transient follow-up previews, never conversation or input history."""

from rich.console import Console
from rich.text import Text


def pending_input_lines(
    messages: tuple[str, ...], width: int, *, edit_enabled: bool = False
) -> list[str]:
    if not messages or width < 4:
        return []
    console = Console(width=width)
    header = Text("Queued follow-up inputs").wrap(console, width - 2)
    lines = [("• " if index == 0 else "  ") + line.plain for index, line in enumerate(header)]
    for message in messages:
        wrapped = []
        for source_line in message.splitlines()[:4]:
            wrapped.extend(Text(source_line).wrap(console, max(1, width - 4)))
            if len(wrapped) > 3:
                break
        lines.extend(
            ("  ↳ " if index == 0 else "    ") + line.plain
            for index, line in enumerate(wrapped[:3])
        )
        if len(wrapped) > 3:
            lines.append("    …")
    if edit_enabled:
        lines.extend(
            "    " + line.plain
            for line in Text("alt+↑ edit last queued message").wrap(console, max(1, width - 4))
        )
    return lines
