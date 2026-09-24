"""Read-only, page-bounded transcript browser; owns no runtime or model."""

import sqlite3

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea

from corki.cli.display_text import visible_terminal_text
from corki.cli.history_projection import HistoryProjection
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
)
from corki.protocol.wire_numbers import dumps_wire


def page_text(page):
    blocks = []
    size = 0
    for item in HistoryProjection(page.leading_replacements).feed(page.items):
        if isinstance(item, UserMessageItem):
            text = "› " + item.content
            if item.attachments:
                text += f"\n[{len(item.attachments)} image attachment(s)]"
        elif isinstance(item, AssistantMessageItem):
            text = "• " + item.content
        elif isinstance(item, ReasoningItem):
            text = "Thinking: " + item.summary if item.summary else ""
        elif isinstance(item, ToolCallItem):
            text = (
                "• "
                + item.call.name
                + " "
                + (item.call.raw_arguments or dumps_wire(item.call.arguments))
            )
        elif isinstance(item, ToolResultItem):
            text = item.display_content if item.display_content is not None else item.content
            if item.is_error or item.exit_code not in (None, 0):
                text += "\n[Tool failed]"
        elif isinstance(item, TurnAbortedItem):
            text = "Turn interrupted."
        else:
            continue
        size += len(text)
        if size > 8_000_000:
            raise ValueError("Transcript page exceeds display capacity")
        blocks.append(visible_terminal_text(text))
    return "\n\n".join(blocks) or "No visible messages on this page."


async def view_session(catalog, thread_id, *, input=None, output=None):
    body = TextArea(read_only=True, scrollbar=True, wrap_lines=True)
    keys = KeyBindings()
    cursors = [None]
    older = None
    loading = False
    status = "Loading session…"

    async def load(cursor, *, append=False, back=False):
        nonlocal older, loading, status
        if loading:
            return
        loading, status = True, "Loading session…"
        app.invalidate()
        try:
            page = await catalog.transcript_page(thread_id, cursor=cursor)
            text = page_text(page)
            if append:
                cursors.append(cursor)
            if back:
                cursors.pop()
            older = page.next_cursor
            body.text = text
            body.buffer.cursor_position = 0 if back else len(text)
            status = "History · ↑↓ scroll · PgUp older / PgDn newer · Esc back"
        except (OSError, ValueError, LookupError, sqlite3.Error) as error:
            status = f"Could not load history ({type(error).__name__}). Esc to return."
        finally:
            loading = False
            app.invalidate()

    for key in ("escape", "c-t", "c-c", "q"):

        @keys.add(key, eager=True)
        def close(event):
            event.app.exit()

    @keys.add("enter", eager=True)
    @keys.add("tab", eager=True)
    def ignore(event):
        pass

    @keys.add("home")
    def home(event):
        body.buffer.cursor_position = 0

    @keys.add("end")
    def end(event):
        body.buffer.cursor_position = len(body.text)

    @keys.add("pageup")
    async def previous(event):
        nonlocal status
        if body.buffer.cursor_position == 0 and older is not None:
            if len(cursors) >= 4096:
                status = "Navigation capacity reached. Esc to return."
            else:
                await load(older, append=True)
        else:
            body.buffer.cursor_up(count=max(1, app.output.get_size().rows - 3))

    @keys.add("pagedown")
    async def next_page(event):
        if body.buffer.cursor_position == len(body.text) and len(cursors) > 1:
            await load(cursors[-2], back=True)
        else:
            body.buffer.cursor_down(count=max(1, app.output.get_size().rows - 3))

    app = Application(
        layout=Layout(
            HSplit(
                [
                    Window(FormattedTextControl(lambda: status), height=1),
                    body,
                ]
            ),
            focused_element=body,
        ),
        key_bindings=keys,
        full_screen=True,
        erase_when_done=True,
        input=input,
        output=output,
    )
    await app.run_async(pre_run=lambda: app.create_background_task(load(None)))
