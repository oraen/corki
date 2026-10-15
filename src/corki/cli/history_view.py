"""Read-only transcript viewport sharing the composer's input application."""

from contextlib import contextmanager
from weakref import WeakSet

from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl

from corki.cli.history_rows import HistoryRows

MAX_HISTORY_CACHE_BYTES = 512 * 1024 * 1024
MAX_DEFERRED_OUTPUT_CHARS = 1_000_000
MAX_DEFERRED_OUTPUT_CHUNKS = 4096


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
        self.render_error = False
        self.lines = [[]]
        self._stores = WeakSet()
        self.deferred_output = []
        self._deferred_chars = 0
        self._deferred_overflow = False
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
            self.render_error = False
            follow_bottom = self.row >= self.rows - 1
            store = None
            try:
                remaining = MAX_HISTORY_CACHE_BYTES - sum(s._size for s in self._stores)
                store = HistoryRows(max_bytes=max(0, remaining))
                self.ui._transcript.render(
                    width, include_reasoning=True, expand_tools=True, output=store
                )
                for fragment in plan_tail:
                    store.append(fragment)
                store.finish()
            except (OSError, ValueError):
                self.render_error = True
                if store is not None:
                    store.close()
                self.lines = [
                    [
                        (
                            "fg:ansired",
                            "History display unavailable (storage or size limit). Esc to return.",
                        )
                    ]
                ]
            else:
                self._stores.add(store)
                self.lines = store
            self.rows = len(self.lines)
            self.row = self.rows - 1 if follow_bottom else min(self.row, self.rows - 1)
            self.cache_key = key
        return self._fragments(self.lines)

    @staticmethod
    def _fragments(lines):
        for index in range(len(lines)):
            if index:
                yield ("", "\n")
            yield from lines[index]

    def _release_rows(self):
        # UIContent frames retain their own immutable store until released. The
        # Window keeps at most eight frames; closing the viewport closes all of
        # them immediately, including frames still referenced by the renderer.
        for store in tuple(self._stores):
            store.close()
        self._stores.clear()
        self.lines = [[]]
        self.cache_key = None

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
            if text and not self._deferred_overflow:
                if (
                    self._deferred_chars + len(text) > MAX_DEFERRED_OUTPUT_CHARS
                    or len(self.deferred_output) >= MAX_DEFERRED_OUTPUT_CHUNKS
                ):
                    # This is a main-screen replay optimization, not history.
                    # Once full, rebuild the screen from canonical source after
                    # closing instead of retaining/dropping arbitrary fragments.
                    self.deferred_output.clear()
                    self._deferred_chars = 0
                    self._deferred_overflow = True
                    self.ui._transcript.repair_pending = True
                else:
                    self.deferred_output.append(text)
                    self._deferred_chars += len(text)
            self.ui._session.app.invalidate()

    def open(self):
        if self.active:
            return
        self.text()
        app = self.ui._session.app
        self.previous_screen_modes = (app.full_screen, app.renderer.full_screen)
        self.previous_focus = self.ui._session.layout.current_control
        # Erase the inline frame before saving the main terminal buffer. A fresh
        # renderer baseline is required for each screen, not just a layout swap.
        try:
            app.renderer.erase()
            app.full_screen = app.renderer.full_screen = True
            self.active = True
            self.row = self.rows - 1
            self.ui._session.layout.focus(self.control)
        except BaseException:
            self._restore_and_release()
            raise
        self.ui._session.app.invalidate()

    def _restore_and_release(self):
        app = self.ui._session.app
        if self.previous_screen_modes is not None:
            app.full_screen, app.renderer.full_screen = self.previous_screen_modes
        self.active = False
        try:
            if self.previous_focus is not None:
                self.ui._session.layout.focus(self.previous_focus)
        finally:
            self.deferred_output.clear()
            self._deferred_chars = 0
            self._deferred_overflow = False
            self._release_rows()
            self.previous_focus = None
            self.previous_screen_modes = None

    def close(self):
        if not self.active:
            self._release_rows()
            return
        app = self.ui._session.app
        # erase/reset also leaves the alternate screen. read_message's finally
        # calls this after cancellation, including approval preemption.
        try:
            app.renderer.erase()
            app.full_screen, app.renderer.full_screen = self.previous_screen_modes
            self.active = False
            if self.deferred_output:
                app.output.enable_autowrap()
                app.output.write_raw("".join(self.deferred_output))
                app.output.flush()
        finally:
            # These are derived viewport caches, not canonical history. Release
            # them even if writing to a disconnected terminal fails. Reopening
            # regenerates at the current width from the retained transcript.
            self._restore_and_release()
        if app.is_running and not app.is_done:
            app._request_absolute_cursor_position()
        self.ui._session.app.invalidate()
