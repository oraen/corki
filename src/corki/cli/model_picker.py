"""Local model selection; browsing never publishes settings."""

from dataclasses import dataclass

from prompt_toolkit.document import Document
from prompt_toolkit.filters import to_filter
from prompt_toolkit.key_binding import KeyBindings
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from corki.cli.menu_overlay import MenuOverlay
from corki.protocol.settings import UNSET, UnsetSetting

BACK = object()


@dataclass(frozen=True)
class ModelSelection:
    model: str
    reasoning_effort: str | None | UnsetSetting = UNSET


async def choose_model_and_effort(session, settings, current, *, overlay_host=None):
    """Stage navigation locally; publish only a fully accepted selection."""
    models = tuple(
        dict.fromkeys((current.model, *(info.model for info in (settings.model_contexts or ()))))
    )
    selected_model = current.model
    common = {"compact": True, "overlay_host": overlay_host}
    descriptions = {
        "none": "No reasoning effort.",
        "minimal": "Minimal reasoning for quick responses.",
        "low": "Faster responses with less reasoning.",
        "medium": "A balance of reasoning depth and speed.",
        "high": "More reasoning for complex tasks.",
        "xhigh": "Extra reasoning for difficult tasks.",
        "max": "Prioritize quality over speed; higher time and token usage.",
        "ultra": "Most intensive reasoning mode; higher time and token usage.",
        "More reasoning…": "Choose Max or Ultra; may increase time and token usage.",
    }
    while True:
        model = await choose_model(
            session,
            current.model,
            models,
            initial=selected_model,
            subtitle="Choose a model, then its reasoning level.",
            **common,
        )
        if model is None:
            return None
        selected_model = model
        info = settings.model_context_info(model)
        levels = info.supported_reasoning_levels
        if not levels:
            return ModelSelection(model)
        if len(levels) == 1 and levels[0] not in {"max", "ultra"}:
            return ModelSelection(model, levels[0])
        active = (
            current.reasoning_effort or info.default_reasoning_level
            if model == current.model
            else None
        )
        normal = tuple(level for level in levels if level not in {"max", "ultra"})
        advanced = tuple(level for level in ("max", "ultra") if level in levels)
        choices = normal + (("More reasoning…",) if advanced else ())
        marked = "More reasoning…" if active in advanced else active
        initial = marked or (
            info.default_reasoning_level if info.default_reasoning_level in normal else choices[0]
        )
        while True:
            effort = await choose_model(
                session,
                marked if marked in choices else None,
                choices,
                title=f"Select Reasoning Level for {model}",
                subtitle="",
                allow_custom=False,
                back=True,
                initial=initial,
                default_choice=info.default_reasoning_level,
                descriptions=descriptions,
                **common,
            )
            if effort is None:
                return None
            if effort is BACK:
                break
            if effort != "More reasoning…":
                return ModelSelection(model, effort)
            effort = await choose_model(
                session,
                active if active in advanced else None,
                advanced,
                title="Advanced Reasoning",
                subtitle="May increase time and token usage.",
                allow_custom=False,
                back=True,
                descriptions=descriptions,
                **common,
            )
            if effort is None:
                return None
            if effort is BACK:
                initial = "More reasoning…"
                continue
            return ModelSelection(model, effort)


async def choose_model(
    session,
    current,
    models,
    *,
    title="Select model",
    subtitle="Configured models (provider-dependent)",
    allow_custom=True,
    overlay_host=None,
    compact=False,
    back=False,
    initial=None,
    default_choice=None,
    descriptions=None,
):
    choices = (
        tuple(dict.fromkeys((current, *models)))
        if not compact
        else tuple(
            dict.fromkeys((*models, *((current,) if current and current not in models else ())))
        )
    )
    highlight = initial if initial is not None else current
    selected = choices.index(highlight) if highlight in choices else 0
    error = ""
    bindings = KeyBindings()

    def matches():
        query = session.default_buffer.text.strip() if allow_custom or not compact else ""
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
    def escape(event):
        event.app.exit(result=BACK if back else None)

    @bindings.add("c-c")
    @bindings.add("c-d")
    def cancel(event):
        event.app.exit(result=None)

    def changed(buffer):
        nonlocal selected, error
        selected = 0
        error = ""

    if compact and not allow_custom:
        for number in range(1, min(9, len(choices)) + 1):

            @bindings.add(str(number))
            def shortcut(event, index=number - 1):
                event.app.exit(result=choices[index])

    def render():
        available = matches()
        size = session.app.output.get_size()
        width = max(1, size.columns - 1)
        console = Console(width=width)

        def rows(text, style=""):
            return [(style, line.plain + "\n") for line in Text(text).wrap(console, width)]

        header = rows(title, "bold")
        if subtitle:
            header += rows(subtitle, "fg:ansibrightblack" if compact else "")
        if compact:
            header += rows("") or [("", "\n")]
        footer = []
        if error:
            footer += rows(error, "fg:ansired")
        elif not available:
            footer += rows(
                "Enter to use this custom model name." if allow_custom else "No matches."
            )
        footer += rows(
            "↑/↓ select · Enter confirm · Esc " + ("back" if back else "cancel"),
            "fg:ansibrightblack" if compact else "",
        )
        prompt = (
            "Search or enter model: "
            if compact and allow_custom
            else ("" if compact else "Model: " if allow_custom else "Filter: ")
        )
        input_rows = 1 + cell_len(prompt + session.default_buffer.text) // width
        budget = max(1, size.rows - len(header) - len(footer) - input_rows - 1)

        def item(index):
            model = available[index]
            label = model + (" (current)" if model == current else "")
            if compact:
                label = f"{index + 1}. " + label
                if model == default_choice:
                    label += " (default)"
            # Model identifiers are literal labels, not terminal control sequences.
            label = "".join(c for c in label if c.isprintable())
            if compact:
                line = Text(label)
                line.truncate(max(1, width - 2), overflow="ellipsis")
                wrapped = [line]
            else:
                wrapped = Text(label).wrap(console, max(1, width - 2))
            result = [
                (
                    "fg:ansicyan bold" if selected == index else "",
                    ("› " if selected == index and row == 0 else "  ") + line.plain + "\n",
                )
                for row, line in enumerate(wrapped)
            ]
            if compact and descriptions and model in descriptions:
                description = " ".join(descriptions[model].split())
                result += [
                    ("fg:ansibrightblack", "    " + line.plain + "\n")
                    for line in Text(description).wrap(console, max(1, width - 4))
                ]
            return result

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
        return header + visible + footer + [("", prompt)]

    previous_read_only = session.default_buffer.read_only
    previous_erase = session.app.erase_when_done
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
            session.default_buffer.read_only = to_filter(compact and not allow_custom)
            session.app.erase_when_done = compact or previous_erase
            try:
                result = await session.prompt_async(
                    render,
                    key_bindings=bindings,
                    default=document,
                    pre_run=restore_selection,
                )
            finally:
                session.default_buffer.read_only = previous_read_only
                session.app.erase_when_done = previous_erase
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
        session.default_buffer.read_only = previous_read_only
        session.app.erase_when_done = previous_erase
