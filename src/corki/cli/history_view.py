"""Read-only transcript viewport sharing the composer's input application."""

from contextlib import contextmanager

from prompt_toolkit import ANSI
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl


class HistoryControl(UIControl):
    """Expose cached styled rows without rebuilding the full transcript each frame."""

    def __init__(self, view):
        self.view = view

    def is_focusable(self):
        return True

    def create_content(self, width, height):
        self.view.text()
        # Capture this frame's rows: a later source update must not change an
        # already returned UIContent's line count or line lookup.
        lines = self.view.lines
        return UIContent(
            get_line=lines.__getitem__,
            line_count=len(lines),
            cursor_position=Point(0, self.view.row),
        )


class HistoryView:
    def __init__(self, ui):
        self.ui = ui
        self.active = False
        self.row = 0
        self.rows = 1
        self.previous_focus = None
        self.previous_screen_modes = None
        self.cache_key = None
        self.content = ""
        self.formatted = []
        self.lines = [[]]
        self.deferred_output = []
        self.loader = None
        self.control = HistoryControl(self)
        self.window = Window(self.control, wrap_lines=False)
        original = ui._session.layout.container
        ui._session.layout.container = HSplit(
            [
                ConditionalContainer(
                    HSplit(
                        [
                            Window(
                                FormattedTextControl(self.title),
                                height=1,
                            ),
                            self.window,
                        ],
                    ),
                    Condition(lambda: self.active),
                ),
                ConditionalContainer(original, Condition(lambda: not self.active)),
            ]
        )
        active = Condition(lambda: self.active)

        @ui._bindings.add(Keys.Any, filter=active)
        @ui._bindings.add("enter", filter=active, eager=True)
        @ui._bindings.add("tab", filter=active, eager=True)
        def ignore(event):
            pass

        @ui._bindings.add("escape", filter=active, eager=True)
        @ui._bindings.add("q", filter=active, eager=True)
        @ui._bindings.add("c-t", filter=active, eager=True)
        @ui._bindings.add("c-c", filter=active, eager=True)
        def close(event):
            self.close()

        navigation = (
            (("up", "k"), -1, "line"),
            (("down", "j"), 1, "line"),
            (("pageup", "c-b"), -1, "page"),
            (("pagedown", " ", "c-f"), 1, "page"),
            (("c-u",), -1, "half"),
            (("c-d",), 1, "half"),
        )
        for keys, amount, distance in navigation:

            def move(event, amount=amount, distance=distance):
                self.text()
                page = max(1, ui._session.app.output.get_size().rows - 4)
                step = {"line": 1, "page": page, "half": (page + 1) // 2}[distance]
                self.row = min(self.rows - 1, max(0, self.row + amount * step))
                if amount < 0 and self.loader is not None and self.row <= page:
                    self.loader.request_older()

            for key in keys:
                ui._bindings.add(key, filter=active, eager=True)(move)

        @ui._bindings.add("home", filter=active, eager=True)
        def start(event):
            self.row = 0
            if self.loader is not None:
                self.loader.request_older(beginning=True)

        @ui._bindings.add("end", filter=active, eager=True)
        def end(event):
            self.text()
            self.row = self.rows - 1

    def title(self):
        suffix = ""
        if self.loader is not None:
            if self.loader.task is not None:
                suffix = " · loading older history"
            elif self.loader.has_older:
                suffix = " · older history available"
        return "History · ↑↓ / PgUp PgDn · Esc to close" + suffix

    def text(self):
        width = self.ui._session.app.output.get_size().columns
        plan = getattr(self.ui, "_plan_stream", None)
        plan_commit = (id(plan), plan.emitted) if plan is not None else None
        # The plan tail is render-only, just as in the inline viewport. Never
        # replay it into committed history or drain pending rows while browsing.
        plan_tail = (
            self.ui._plan_tail_fragments()
            if plan is not None and getattr(self.ui, "_stream_markdown", None) is None
            else []
        )
        key = (
            width,
            tuple(id(entry) for entry in self.ui._transcript.calls),
            plan_commit,
            tuple(plan_tail),
        )
        if key != self.cache_key:
            follow_bottom = self.row >= self.rows - 1
            self.content = self.ui._transcript.render(width, include_reasoning=True)
            self.formatted = to_formatted_text(ANSI(self.content))
            self.formatted.extend(plan_tail)
            self.lines = list(split_lines(self.formatted))
            self.rows = len(self.lines)
            self.row = self.rows - 1 if follow_bottom else min(self.row, self.rows - 1)
            self.cache_key = key
        return self.formatted

    @contextmanager
    def capture_output(self):
        """Keep display state current without writing into another screen."""
        if not self.active or self.ui._transcript.replaying:
            yield
            return
        captured = self.ui._console.capture()
        try:
            with captured:
                yield
        finally:
            text = captured.get()
            if text:
                self.deferred_output.append(text)
            self.ui._session.app.invalidate()

    def open(self):
        if self.active:
            return
        self.text()
        app = self.ui._session.app
        self.previous_screen_modes = (app.full_screen, app.renderer.full_screen)
        # Erase the inline frame before saving the main terminal buffer. A fresh
        # renderer baseline is required for each screen, not just a layout swap.
        app.renderer.erase()
        app.full_screen = app.renderer.full_screen = True
        self.previous_focus = self.ui._session.layout.current_control
        self.active = True
        self.row = self.rows - 1
        self.ui._session.layout.focus(self.control)
        self.ui._session.app.invalidate()

    def close(self):
        if not self.active:
            return
        app = self.ui._session.app
        # erase/reset also leaves the alternate screen. read_message's finally
        # calls this after cancellation, including approval preemption.
        app.renderer.erase()
        app.full_screen, app.renderer.full_screen = self.previous_screen_modes
        self.active = False
        self.ui._session.layout.focus(self.previous_focus)
        if self.deferred_output:
            app.output.enable_autowrap()
            app.output.write_raw("".join(self.deferred_output))
            app.output.flush()
            self.deferred_output.clear()
        if app.is_running and not app.is_done:
            app._request_absolute_cursor_position()
        self.ui._session.app.invalidate()
