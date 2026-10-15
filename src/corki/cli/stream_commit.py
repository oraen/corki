"""Owned stream display commits, serialized with the active composer."""

import asyncio
import logging

from prompt_toolkit.application.current import set_app
from prompt_toolkit.application.run_in_terminal import in_terminal
from prompt_toolkit.patch_stdout import StdoutProxy


async def commit_delta(ui, delta, *, operation=None):
    action = operation if operation is not None else lambda: ui.append_assistant_delta(delta)
    view = getattr(ui, "_history_view", None)
    if view is not None and view.active:
        with view.capture_output():
            action()
        return
    console = ui._console
    if not (
        console.is_terminal
        and ui._live_input_enabled
        and isinstance(console.file, StdoutProxy)
        and "\n" in delta
    ):
        action()
        return
    app = ui._session.app

    async def commit():
        # in_terminal is a no-op before the prompt starts. Still write directly
        # then, so its first redraw cannot overtake a queued stdout prefix.
        primary = None
        try:
            with set_app(app):
                async with in_terminal():
                    try:
                        # Capture synchronously: no other event can write into this
                        # capture or redraw the new tail before its prefix is flushed.
                        with console.capture() as captured:
                            action()
                        text = captured.get()
                        if text:
                            app.output.enable_autowrap()
                            app.output.write_raw(text)
                            app.output.flush()
                    except BaseException as error:
                        primary = error
                        raise
        except BaseException as error:
            if primary is not None and error is not primary:
                if isinstance(error, asyncio.CancelledError):
                    raise
                logging.getLogger(__name__).warning(
                    "Terminal restore failed after stream commit failure: %s", type(error).__name__
                )
                raise primary from error
            raise

    task = asyncio.create_task(commit(), name="corki-stream-commit")
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger(__name__).warning("Stream commit failed during cancellation")
        raise
