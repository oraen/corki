"""CLI-only command titles and bounded, indented tool evidence.

Codex exec_cell separates compact previews from retained transcript source. These
helpers never interpret commands for execution or discard canonical event data.
"""

import json
from collections import deque

from rich.cells import cell_len
from rich.text import Text

from corki.cli.display_text import visible_terminal_text
from corki.cli.syntax import highlight_code

PREVIEW_ROWS = 5


def command_title(name, arguments, width, *, light=False):
    """Summarize a complete shell call; malformed previews keep the raw header."""
    if name not in {"exec_command", "shell"} or len(arguments) > 100_000:
        return None
    try:
        data = json.loads(arguments)
    except (ValueError, TypeError):
        return None
    command = data.get("cmd", data.get("command")) if isinstance(data, dict) else None
    if not isinstance(command, str) or not command.strip():
        return None
    command = visible_terminal_text(command)
    first, separator, _ = command.partition("\n")
    # Bound lexing before formatting; disclosure retains the original parameters.
    clipped = Text(first[:2048])
    clipped.truncate(max(1, width - 2), overflow="ellipsis")
    code = (
        highlight_code(clipped.plain, "shell", theme="default" if light else "monokai") or clipped
    )
    # Durable scrollback is not a mutable Codex cell. Keep the title neutral;
    # the existing turn-owned status reports Running until completion/cancel.
    result = Text.assemble(("• ", "dim"), code)
    if separator or len(first) > 2048:
        result.append(" …", style="dim")
    result.truncate(max(1, width), overflow="ellipsis")
    return result


def output_rows(console, output):
    """Wrap one logical line at a time, rather than allocate all screen rows."""
    text = visible_terminal_text(output).removesuffix("\n")
    start = 0
    width = max(1, console.width - 4) if console.width >= 5 else max(1, console.width)
    while True:
        end = text.find("\n", start)
        line = text[start:] if end < 0 else text[start:end]
        if "\t" not in line and cell_len(line) <= width:
            yield Text(line)
        else:
            yield from Text(line).wrap(console, width, overflow="fold", no_wrap=False)
        if end < 0:
            break
        start = end + 1


def compact_rows(rows):
    """Two leading/two trailing screen rows and an accurate omission marker."""
    head, tail, count = [], deque(maxlen=3), 0
    for row in rows:
        if count < 2:
            head.append(row)
        else:
            tail.append(row)
        count += 1
    if count <= PREVIEW_ROWS:
        yield from head
        yield from tail
    else:
        yield from head
        yield Text(f"… +{count - 4} lines (ctrl+t to expand)")
        yield from list(tail)[-2:]


def prefixed_row(console, row, *, first=False):
    prefix = ("  └ " if first else "    ") if console.width >= 5 else ""
    row = Text.assemble((prefix, "dim"), row)
    row.truncate(max(1, console.width), overflow="ellipsis")
    return row


def print_row(console, row, *, first=False):
    console.print(
        prefixed_row(console, row, first=first), style="dim", markup=False, highlight=False
    )


def print_output(console, output, *, expanded=False):
    rows = output_rows(console, output)
    if not expanded:
        rows = compact_rows(rows)
    batch = []
    for index, row in enumerate(rows):
        prefix = ("  └ " if index == 0 else "    ") if console.width >= 5 else ""
        text = prefix + row.plain
        if cell_len(text) > console.width:
            clipped = Text(text)
            clipped.truncate(max(1, console.width), overflow="ellipsis")
            text = clipped.plain
        batch.append(text)
        if len(batch) == 64:
            console.print(Text("\n".join(batch)), style="dim", soft_wrap=True)
            batch.clear()
    if batch:
        console.print(Text("\n".join(batch)), style="dim", soft_wrap=True)
