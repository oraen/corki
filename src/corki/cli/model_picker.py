"""Local model selection; browsing never publishes settings."""

from dataclasses import dataclass

from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from corki.cli.menu_overlay import MenuOverlay
from corki.protocol.settings import UNSET, UnsetSetting


@dataclass(frozen=True)
class ModelSelection:
    model: str
    reasoning_effort: str | None | UnsetSetting = UNSET


async def choose_model(
    session,
    current,
    models,
    *,
    title="Select model",
    subtitle="Configured models (provider-dependent)",
    allow_custom=True,
    overlay_host=None,
):
    choices = tuple(dict.fromkeys((current, *models)))
    selected = 0
    error = ""
    bindings = KeyBindings()

    def matches():
        query = session.default_buffer.text.strip()
        return tuple(m for m in choices if query.lower() in m.lower())

    @bindings.add("up")
    @bindings.add("c-p")
    def previous(event):
        nonlocal selected
        selected = max(0, selected - 1)

    @bindings.add("down")
    @bindings.add("c-n")
    def following(event):
        nonlocal selected
        selected = min(max(0, len(matches()) - 1), selected + 1)

    @bindings.add("enter")
    def confirm(event):
        nonlocal error
        available = matches()
        text = event.current_buffer.text.strip()
        if not available and not allow_custom:
            error = "Choose a supported reasoning effort."
            return
        value = available[min(selected, len(available) - 1)] if available else text
        if value and (not allow_custom or all(c.isprintable() and not c.isspace() for c in value)):
            event.app.exit(result=value)
        else:
            error = "Model names cannot contain whitespace or control characters."

    @bindings.add("escape", eager=True)
    @bindings.add("c-c")
    @bindings.add("c-d")
    def cancel(event):
        event.app.exit(result=None)

    def changed(buffer):
        nonlocal selected, error
        selected = 0
        error = ""

    def render():
        available = matches()
        size = session.app.output.get_size()
        width = max(1, size.columns - 1)
        console = Console(width=width)

        def rows(text, style=""):
            return [(style, line.plain + "\n") for line in Text(text).wrap(console, width)]

        header = rows(title, "bold")
        header += rows(subtitle)
        footer = []
        if error:
            footer += rows(error, "fg:ansired")
        elif not available:
            footer += rows(
                "Enter to use this custom model name." if allow_custom else "No matches."
            )
        footer += rows("↑/↓ select · Enter confirm · Esc cancel")
        input_rows = 1 + cell_len("Model: " + session.default_buffer.text) // width
        budget = max(1, size.rows - len(header) - len(footer) - input_rows - 1)

        def item(index):
            model = available[index]
            label = model + (" (current)" if model == current else "")
            # Model identifiers are literal labels, not terminal control sequences.
            label = "".join(c for c in label if c.isprintable())
            wrapped = Text(label).wrap(console, max(1, width - 2))
            return [
                (
                    "fg:ansicyan bold" if selected == index else "",
                    ("› " if selected == index and row == 0 else "  ") + line.plain + "\n",
                )
                for row, line in enumerate(wrapped)
            ]

        visible = []
        if available:
            visible = item(selected)
            if len(visible) > budget:
                visible = visible[:budget]
                visible[-1] = (visible[-1][0], "…\n")
            else:
                for index in range(selected - 1, -1, -1):
                    candidate = item(index)
                    if len(visible) + len(candidate) > budget:
                        break
                    visible = candidate + visible
                for index in range(selected + 1, len(available)):
                    candidate = item(index)
                    if len(visible) + len(candidate) > budget:
                        break
                    visible += candidate
        return header + visible + footer + [("", "Model: " if allow_custom else "Filter: ")]

    session.default_buffer.on_text_changed += changed
    try:
        document = Document()
        while True:
            saved_selection, saved_error = selected, error

            def restore_selection(saved_selection=saved_selection, saved_error=saved_error):
                nonlocal selected, error
                selected, error = saved_selection, saved_error

            if overlay_host is not None:
                overlay_host.active = True
            try:
                result = await session.prompt_async(
                    render, key_bindings=bindings, default=document, pre_run=restore_selection
                )
            finally:
                if overlay_host is not None:
                    overlay_host.active = False
            if not isinstance(result, MenuOverlay):
                return result
            document = session.default_buffer.document
            # Approval uses the same prompt session but cannot edit menu state.
            session.default_buffer.on_text_changed -= changed
            try:
                await result.run()
            finally:
                session.default_buffer.on_text_changed += changed
    except (KeyboardInterrupt, EOFError):
        return None
    finally:
        session.default_buffer.on_text_changed -= changed
        session.default_buffer.reset()
