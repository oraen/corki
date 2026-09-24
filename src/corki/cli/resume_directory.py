"""Resolve the working directory before constructing resumed runtime resources."""

import sys
import tomllib
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from corki.cli.display_text import visible_terminal_text
from corki.config.toml_edits import set_config_value
from corki.storage.sqlite import _joined_write


def read_resume_mode(path):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(1_000_001)
    except FileNotFoundError:
        return None
    if len(data) > 1_000_000:
        raise ValueError("Configuration exceeds resume preference read limit.")
    document = tomllib.loads(data.decode("utf-8"))
    tui = document.get("tui", {})
    if not isinstance(tui, dict):
        raise ValueError("tui must be a table")
    mode = tui.get("resume_cwd")
    if mode not in (None, "current", "session"):
        raise ValueError("tui.resume_cwd must be current or session")
    return mode


async def choose_resume_directory(current, saved, *, config_file=None, input=None, output=None):
    current, saved = Path(current).resolve(), Path(saved).resolve()
    if config_file is not None:
        mode = await _joined_write(read_resume_mode, config_file)
        if mode is not None:
            directory = saved if mode == "session" else current
            if not directory.is_dir():
                raise ValueError("Remembered working directory is unavailable.")
            return directory
    if current == saved:
        return saved
    selected = 0
    error = ""
    choices = (saved, current, saved, current) if config_file is not None else (saved, current)
    keys = KeyBindings()

    def accept(event, index):
        nonlocal error
        if not choices[index].is_dir():
            error = "Selected directory is unavailable. Choose another directory."
            return
        event.app.exit(result=index)

    @keys.add("up")
    @keys.add("down")
    @keys.add("j")
    @keys.add("k")
    def move(event):
        nonlocal selected, error
        delta = -1 if event.key_sequence[-1].key in {"up", "k"} else 1
        selected = (selected + delta) % len(choices)
        error = ""

    @keys.add("enter")
    def enter(event):
        accept(event, selected)

    @keys.add("escape")
    @keys.add("1")
    def session(event):
        accept(event, 0)

    @keys.add("2")
    def here(event):
        accept(event, 1)

    @keys.add("3")
    def remember_session(event):
        if config_file is not None:
            accept(event, 2)

    @keys.add("4")
    def remember_current(event):
        if config_file is not None:
            accept(event, 3)

    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event):
        event.app.exit(result=None)

    def render():
        rows = [("bold", "Choose a working directory to resume this session\n\n")]
        names = (
            "Session directory",
            "Current directory",
            "Session directory, and remember",
            "Current directory, and remember",
        )
        for index, (name, path) in enumerate(zip(names[: len(choices)], choices, strict=True)):
            rows.append(
                (
                    "reverse" if index == selected else "",
                    f"{'›' if index == selected else ' '} {index + 1}. {name}\n"
                    f"   {visible_terminal_text(str(path))}\n",
                )
            )
        if error:
            rows.append(("fg:ansired", error + "\n"))
        rows.append(("fg:ansibrightblack", "↑↓ select · Enter confirm · Esc session · Ctrl+C exit"))
        return rows

    app = Application(
        layout=Layout(Window(FormattedTextControl(render), wrap_lines=True)),
        key_bindings=keys,
        input=input,
        output=output,
        erase_when_done=True,
    )
    result = await app.run_async()
    if result is None:
        return None
    if result >= 2:
        try:
            await _joined_write(
                set_config_value,
                config_file,
                ("tui", "resume_cwd"),
                "session" if result == 2 else "current",
            )
        except (OSError, ValueError):
            print("Failed to save working directory preference.", file=sys.stderr)
    return choices[result]
