"""Local resume picker with bounded async listing and archive actions."""

import asyncio
import sqlite3

from prompt_toolkit.application import Application
from prompt_toolkit.application.run_in_terminal import in_terminal
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea
from rich.text import Text

from corki.cli.display_text import visible_terminal_text


async def choose_session(catalog, cwd, *, input=None, output=None):
    rows, preview = [], []
    selected = offset = toolbar = generation = 0
    all_dirs = archived = more = busy = False
    error = ""
    operation = ""
    load_task = None
    preview_task = None
    preview_open = False
    preview_generation = 0
    search = TextArea(height=1, prompt="Search: ", multiline=False)
    bindings = KeyBindings()

    def loading():
        return load_task is not None and not load_task.done()

    def clip(text):
        value = Text(visible_terminal_text(text).replace("\n", " "))
        value.truncate(max(1, app.output.get_size().columns - 2), overflow="ellipsis")
        return value.plain

    def render():
        height = max(1, min(12, app.output.get_size().rows - 10))
        top = max(0, selected - height + 1)
        parts = [
            ("bold", "Resume a session\n"),
            (
                "",
                f"{'›' if toolbar == 0 else ' '}Directory: {'All' if all_dirs else 'Current'}  "
                f"{'›' if toolbar == 1 else ' '}Status: {'Archived' if archived else 'Active'}\n",
            ),
        ]
        if error:
            parts.append(("fg:ansired", clip(error) + "\n"))
        if operation:
            parts.append(("class:dim", clip(operation) + "\n"))
        if loading():
            parts.append(("class:dim", "Loading sessions…\n"))
        elif not rows:
            parts.append(("class:dim", "No matching sessions.\n"))
        for i, row in enumerate(rows[top : top + height], top):
            label = row.preview or str(row.thread_id)
            parts.append(
                (
                    "reverse" if i == selected else "",
                    clip(("› " if i == selected else "  ") + row.updated_at[:10] + " " + label)
                    + "\n",
                )
            )
        if preview_open:
            parts.append(("bold", "\nPreview (recent messages)\n"))
            if not preview:
                preview_loading = preview_task is not None and not preview_task.done()
                parts.append(("class:dim", "Loading…\n" if preview_loading else "No messages.\n"))
            for kind, content in preview[-6:]:
                parts.append(
                    ("", clip(("› " if kind == "user_message" else "• ") + content) + "\n")
                )
        action = "restore" if archived else "resume"
        parts.append(("class:dim", clip(f"↑↓ select · Enter {action} · Esc clear/back") + "\n"))
        parts.append(
            ("class:dim", clip("Tab/←→ filters" + ("" if archived else " · Ctrl+A archive")) + "\n")
        )
        parts.append(("class:dim", clip("Ctrl+E preview · Ctrl+T transcript") + "\n"))
        return parts

    def close_preview():
        nonlocal preview, preview_open, preview_generation
        preview_generation += 1
        preview, preview_open = [], False
        if preview_task is not None:
            preview_task.cancel()

    def refresh():
        nonlocal generation, load_task
        generation += 1
        close_preview()
        token = generation
        if load_task is not None:
            load_task.cancel()

        async def load():
            nonlocal rows, more, selected, error, preview
            try:
                await asyncio.sleep(0.075)
                page = await catalog.page(
                    cwd=None if all_dirs else cwd,
                    archived=archived,
                    query=search.text[:1000],
                    offset=offset,
                )
                if token == generation:
                    rows, more = list(page.rows), page.has_more
                    selected = min(selected, max(0, len(rows) - 1))
                    preview, error = [], ""
            except (OSError, ValueError, LookupError, sqlite3.Error) as exc:
                if token == generation:
                    rows, preview, error = (
                        [],
                        [],
                        f"Could not load sessions ({type(exc).__name__}).",
                    )
            finally:
                app.invalidate()

        load_task = app.create_background_task(load())

    def query_changed(_):
        nonlocal offset, selected
        offset = selected = 0
        refresh()

    search.buffer.on_text_changed += query_changed

    @bindings.add("up")
    @bindings.add("c-p")
    def up(event):
        nonlocal selected, offset, preview
        if busy or loading():
            return
        close_preview()
        if selected:
            selected -= 1
        elif offset:
            offset = max(0, offset - 50)
            selected = 49
            refresh()

    @bindings.add("down")
    @bindings.add("c-n")
    def down(event):
        nonlocal selected, offset, preview
        if busy or loading():
            return
        close_preview()
        if selected + 1 < len(rows):
            selected += 1
        elif more:
            offset += 50
            selected = 0
            refresh()

    @bindings.add("tab")
    @bindings.add("s-tab")
    def focus_toolbar(event):
        nonlocal toolbar
        toolbar = 1 - toolbar

    @bindings.add("left")
    @bindings.add("right")
    def toggle_filter(event):
        nonlocal all_dirs, archived, offset, selected
        if toolbar == 0:
            all_dirs = not all_dirs
        else:
            archived = not archived
        offset = selected = 0
        refresh()

    @bindings.add("escape")
    def back(event):
        nonlocal preview
        if preview_open:
            close_preview()
        elif search.text:
            search.text = ""
        else:
            event.app.exit(result=None)

    @bindings.add("c-c")
    @bindings.add("c-d")
    def cancel(event):
        event.app.exit(result=None)

    @bindings.add("c-t")
    async def show_transcript(event):
        nonlocal busy
        from corki.cli.session_transcript import view_session

        if busy or not rows or (load_task is not None and not load_task.done()):
            return
        close_preview()
        thread_id = rows[selected].thread_id
        busy = True
        try:
            async with in_terminal():
                await view_session(catalog, thread_id, input=app.input, output=app.output)
        finally:
            busy = False
            app.invalidate()

    @bindings.add("c-e")
    async def show_preview(event):
        nonlocal preview_task, preview_open, preview_generation, error
        if busy or loading():
            return
        if preview_open:
            close_preview()
            return
        if not rows:
            return
        preview_open, error = True, ""
        preview_generation += 1
        preview_token = preview_generation
        thread_id, token = rows[selected].thread_id, generation
        if preview_task is not None:
            preview_task.cancel()

        async def load():
            nonlocal preview, error
            try:
                value = await catalog.preview(thread_id, limit=6)
                if (
                    preview_token == preview_generation
                    and token == generation
                    and rows
                    and rows[selected].thread_id == thread_id
                ):
                    preview = list(value)
            except (OSError, ValueError, LookupError, sqlite3.Error) as exc:
                if (
                    preview_token == preview_generation
                    and token == generation
                    and rows
                    and rows[selected].thread_id == thread_id
                ):
                    error = f"Could not preview session ({type(exc).__name__})."
            finally:
                app.invalidate()

        preview_task = app.create_background_task(load())

    @bindings.add("enter")
    @bindings.add("c-a")
    async def accept_or_archive(event):
        nonlocal busy, error, operation
        if busy or not rows or (load_task is not None and not load_task.done()):
            return
        row = rows[selected]
        archive_action = event.key_sequence[-1].key.value == "c-a"
        if archive_action and archived:
            return
        busy = True
        operation = (
            "Archiving session…"
            if archive_action
            else "Restoring session…"
            if archived
            else "Opening session…"
        )
        error = ""
        app.invalidate()
        try:
            if archive_action:
                await catalog.archive(row.thread_id)
                refresh()
            else:
                if archived:
                    await catalog.unarchive(row.thread_id)
                else:
                    await catalog.archive_store.read(row.thread_id)
                if not app.is_done:
                    app.exit(result=str(row.thread_id))
        except (OSError, ValueError, LookupError, RuntimeError, sqlite3.Error) as exc:
            error = f"Session operation failed ({type(exc).__name__})."
        finally:
            busy = False
            operation = ""
            app.invalidate()

    app = Application(
        layout=Layout(
            HSplit(
                [
                    Window(FormattedTextControl(render), dont_extend_height=True),
                    search,
                ]
            ),
            focused_element=search,
        ),
        key_bindings=bindings,
        full_screen=False,
        erase_when_done=True,
        input=input,
        output=output,
    )
    try:
        return await app.run_async(pre_run=refresh)
    finally:
        generation += 1
        search.buffer.on_text_changed -= query_changed
