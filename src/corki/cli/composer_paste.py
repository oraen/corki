"""Per-composer key dispatch adapter for PasteBurst; no global parser mutation."""

import asyncio
import sys
from time import monotonic
from types import SimpleNamespace

from prompt_toolkit.keys import Keys

from corki.cli.composer_validation import MAX_USER_INPUT_TEXT_CHARS
from corki.cli.paste_burst import PasteBurst


class ComposerPaste:
    def __init__(self, ui, paste):
        self.ui = ui
        self.paste = paste
        self.state = PasteBurst(windows=sys.platform == "win32")
        self.timer = None
        processor = ui._session.app.key_processor
        original = processor._call_handler

        def dispatch(handler, key_sequence):
            if self._owns_input() and self.handle(key_sequence):
                return
            return original(handler, key_sequence)

        processor._call_handler = dispatch

    def _owns_input(self):
        ui = self.ui
        return (
            ui._image_tracking
            and not ui._history_view.active
            and ui._session.layout.current_buffer is ui._session.default_buffer
        )

    def emit(self, text, *, typed=False, decode_paths=True):
        if not text:
            return
        self.ui._last_composer_activity = monotonic()
        if typed:
            self.ui._session.default_buffer.insert_text(text)
        else:
            self.paste(
                SimpleNamespace(
                    data=text,
                    current_buffer=self.ui._session.default_buffer,
                    app=self.ui._session.app,
                    decode_paths=decode_paths,
                    preflight_paths=True,
                )
            )

    def cancel_timer(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def schedule(self):
        self.cancel_timer()
        if self.state.active:
            self.timer = asyncio.get_running_loop().call_later(
                self.state.idle_timeout + 0.001, self.flush
            )

    def flush_full_burst(self):
        # Bound the transient per-character list without discarding user input.
        # Legal-size messages retain a single folded paste. An oversized burst
        # spills into editable draft elements; submission validation still rejects
        # its expanded length. Keep timing/Enter suppression until the burst ends.
        if len(self.state.buffer) >= MAX_USER_INPUT_TEXT_CHARS:
            text = "".join(self.state.buffer)
            self.state.buffer.clear()
            self.emit(text, decode_paths=False)

    def flush(self):
        self.timer = None
        if not self._owns_input():
            return
        result = self.state.flush_due(monotonic())
        if result is not None:
            self.emit(result.text, typed=result.kind == "typed")
        self.schedule()

    def finish(self):
        """Drain synchronously before prompt cancellation; never start a new worker."""
        self.cancel_timer()
        text = self.state.drain_before_modified()
        if text:
            self.emit(text, decode_paths=False)
        self.state.clear()

    def handle(self, sequence):
        if len(sequence) == 1 and sequence[0].key == Keys.CPRResponse:
            return False
        now = monotonic()
        result = self.state.flush_due(now)
        if result is not None:
            self.emit(result.text, typed=result.kind == "typed")
        key = sequence[0].key if len(sequence) == 1 else None
        if (
            isinstance(key, str)
            and not isinstance(key, Keys)
            and len(key) == 1
            and key.isprintable()
        ):
            self.ui._last_composer_activity = now
            if not key.isascii() and not self.state.buffering and self.state.pending is not None:
                self.emit(self.state.drain_before_modified())
            decision, count = self.state.char(key, now, hold=key.isascii())
            buffer = self.ui._session.default_buffer
            if decision in {"append", "pending"}:
                self.state.append(key, now)
            elif decision == "retro":
                # Never retract owned image/paste markers into plain text.
                cursor = buffer.cursor_position
                start = max(0, cursor - count)
                overlaps = any(
                    e.start < cursor and e.end > start for e in self.ui._inline_images.elements
                )
                start = (
                    None if overlaps else self.state.retro_grab(buffer.text[:cursor], count, now)
                )
                if start is None:
                    buffer.insert_text(key)
                else:
                    buffer.delete_before_cursor(cursor - start)
                    self.state.append(key, now)
            elif decision == "insert":
                buffer.insert_text(key)
            self.flush_full_burst()
            self.schedule()
            return True
        if key == Keys.ControlM and self.state.suppress_enter(now):
            if not self.state.newline(now):
                self.ui._session.default_buffer.insert_text("\n")
                self.state.window_until = now + 0.120
            self.flush_full_burst()
            self.schedule()
            return True
        self.cancel_timer()
        text = self.state.drain_before_modified()
        if text:
            self.emit(text, decode_paths=False)
        self.state.clear()
        return False
