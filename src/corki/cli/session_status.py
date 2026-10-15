"""Width-bounded model and working-directory footer, independent of activity."""

import os
from pathlib import Path

from prompt_toolkit.output import ColorDepth
from rich.cells import cell_len
from rich.text import Text

from corki.cli.display_text import visible_terminal_text


def status_color_depth():
    # iTerm supports RGB, but prompt-toolkit's xterm default quantizes to 256
    # colors. Keep explicit user overrides (including NO_COLOR) authoritative.
    return ColorDepth.from_env() or (
        ColorDepth.DEPTH_24_BIT if os.environ.get("TERM_PROGRAM") == "iTerm.app" else None
    )


def _accent(rgb):
    # Codex status_line_style.rs: 85% saturation, 100% brightness, integer rounding.
    r, g, b = rgb
    luma = (77 * r + 150 * g + 29 * b) // 256
    channels = [(channel * 85 + luma * 15 + 50) // 100 for channel in rgb]
    return "fg:#" + "".join(f"{channel:02x}" for channel in channels)


def session_status(model, directory, width, *, light=False, reasoning_effort=None):
    if width <= 0:
        return []
    # Codex's default syntax themes: Catppuccin Latte / Mocha, type and string scopes.
    model_style = _accent((223, 142, 29) if light else (249, 226, 175))
    path_style = _accent((64, 160, 43) if light else (166, 227, 161))
    model = " ".join(visible_terminal_text(model).split())
    if reasoning_effort is not None:
        effort = " ".join(visible_terminal_text(reasoning_effort).split())
        model = f"{model} {effort}"
    try:
        path = "~/" + str(directory.relative_to(Path.home()))
    except ValueError:
        path = str(directory)
    path = " ".join(visible_terminal_text(path).split())
    padding = "  " if width >= 8 else ""
    available = width - len(padding)
    if available < 7:
        label = Text(model)
        label.truncate(available, overflow="ellipsis")
        return [(model_style, padding + label.plain)]
    # A short path should not force an otherwise fitting model/effort to truncate.
    model_width = min(cell_len(model), max(1, (available - 3) // 2, available - 3 - cell_len(path)))
    path_width = available - 3 - model_width
    label = Text(model)
    label.truncate(model_width, overflow="ellipsis")
    if cell_len(path) > path_width:
        while path and cell_len(path) > path_width - 1:
            path = path[1:]
        path = "…" + path
    return [
        ("", padding),
        (model_style, label.plain),
        ("fg:ansibrightblack", " · "),
        (path_style, path),
    ]
