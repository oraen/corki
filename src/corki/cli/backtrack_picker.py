"""Read-only transcript selection for source-preserving prompt forks."""

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.widgets import TextArea

from corki.cli.backtrack_pages import BacktrackPages


async def choose_prompt(history, prompts, *, input=None, output=None):
    if not prompts:
        return None
    pages = BacktrackPages(history, prompts)
    cursor = (0, 0)
    next_cursor = None
    targets = {}
    selected = len(prompts) - 1
    body = TextArea(read_only=True, scrollbar=True, wrap_lines=True)

    class Highlight(Processor):
        def apply_transformation(self, transformation_input):
            target = targets.get(pages.prompts[selected])
            if target is None:
                return Transformation(transformation_input.fragments)
            start, end = target
            document = transformation_input.document
            first = document.translate_index_to_position(start)[0]
            last = document.translate_index_to_position(max(start, end - 1))[0]
            fragments = transformation_input.fragments
            if first <= transformation_input.lineno <= last:
                fragments = [
                    (style + " reverse", value, *rest) for style, value, *rest in fragments
                ]
            return Transformation(fragments)

    body.control.input_processors.append(Highlight())
    keys = KeyBindings()

    def show(position, *, bottom=False):
        nonlocal cursor, targets, next_cursor
        cursor = position
        text, targets, next_cursor = pages.page(cursor)
        body.text = text
        body.buffer.cursor_position = len(text) if bottom else 0

    def select(index):
        nonlocal selected
        selected = max(0, min(index, len(prompts) - 1))
        show((pages.prompts[selected], 0))

    @keys.add("pageup", eager=True)
    def page_up(event):
        if body.buffer.cursor_position == 0 and cursor != (0, 0):
            show(pages.previous(cursor), bottom=True)
        else:
            body.buffer.cursor_up(count=max(1, event.app.output.get_size().rows - 3))

    @keys.add("pagedown", eager=True)
    def page_down(event):
        if body.buffer.cursor_position == len(body.text) and next_cursor is not None:
            show(next_cursor)
        else:
            body.buffer.cursor_down(count=max(1, event.app.output.get_size().rows - 3))

    @keys.add("escape", eager=True)
    @keys.add("left", eager=True)
    def older(event):
        select(selected - 1)

    @keys.add("right", eager=True)
    def newer(event):
        select(selected + 1)

    @keys.add("enter", eager=True)
    def confirm(event):
        event.app.exit(result=selected)

    for key in ("q", "c-t", "c-c"):

        @keys.add(key, eager=True)
        def close(event):
            event.app.exit(result=None)

    @keys.add("tab", eager=True)
    def ignore(event):
        pass

    def footer():
        return (
            f"Edit previous prompt · {selected + 1}/{len(prompts)}\n"
            "Esc/← older · → newer · Enter edit\n"
            "q / Ctrl+T close · ↑↓ scroll · PgUp/PgDn history"
        )

    app = Application(
        layout=Layout(
            HSplit([body, Window(FormattedTextControl(footer), height=3)]),
            focused_element=body,
        ),
        key_bindings=keys,
        full_screen=True,
        erase_when_done=True,
        input=input,
        output=output,
    )
    select(selected)
    return await app.run_async()
