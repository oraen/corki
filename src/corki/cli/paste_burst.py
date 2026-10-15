"""Codex-style non-bracketed paste classification, independent of terminal ownership.

ASCII holds its first character for 8ms; a second fast character starts a burst.
IME characters are never held: callers may retro-grab a paste-like prefix after
three fast characters. Flush returns either typed text or a paste; only the latter
should enter the composer's path/image/large-paste handling. Enter is suppressed
for 120ms after burst activity, including after flushing. Modified keys must drain
the buffer before resetting timing. Explicit paste and cancellation clear all state.

Times are monotonic seconds supplied by the owner, so tests need no real sleeps.
This module owns no task or timer; a composer adapter must schedule/cancel flushes.
"""

from dataclasses import dataclass

CHAR_INTERVAL = 0.008
ENTER_WINDOW = 0.120


@dataclass(frozen=True)
class Flush:
    kind: str
    text: str


class PasteBurst:
    def __init__(self, *, windows=False):
        self.idle_timeout = 0.060 if windows else CHAR_INTERVAL
        self.clear()

    def clear(self):
        self.last_char = None
        self.count = 0
        self.window_until = None
        self.buffer = []
        self.buffering = False
        self.pending = None

    @property
    def active(self):
        return self.buffering or bool(self.buffer) or self.pending is not None

    def _note(self, now):
        self.count = (
            min(65535, self.count + 1)
            if (self.last_char is not None and now - self.last_char <= CHAR_INTERVAL)
            else 1
        )
        self.last_char = now

    def char(self, ch, now, *, hold=True):
        """Return ('hold'|'append'|'pending'|'retro'|'insert', retro_count).

        Owner flushes overdue text before calling this method. For append/pending,
        call append(ch, now); pending has already moved the held first char.
        For retro, call retro_grab(before_cursor, count, now), remove that slice
        if accepted, then append the current char. Otherwise insert normally.
        """
        self._note(now)
        if self.buffering:
            self.window_until = now + ENTER_WINDOW
            return "append", 0
        if hold and self.pending is not None and now - self.pending[1] <= CHAR_INTERVAL:
            self.buffering = True
            self.buffer.append(self.pending[0])
            self.pending = None
            self.window_until = now + ENTER_WINDOW
            return "pending", 0
        if self.count >= 3:
            return "retro", self.count - 1
        if hold:
            self.pending = ch, now
            return "hold", 0
        return "insert", 0

    def append(self, ch, now):
        self.buffer.append(ch)
        self.window_until = now + ENTER_WINDOW

    def retro_grab(self, before, count, now):
        # Python offsets are Unicode characters, unlike Rust's UTF-8 byte offsets.
        start = max(0, len(before) - count)
        grabbed = before[start:]
        if not (len(grabbed) >= 16 or any(c.isspace() for c in grabbed)):
            return None
        self.buffer.extend(grabbed)
        self.buffering = True
        self.window_until = now + ENTER_WINDOW
        return start

    def flush_due(self, now):
        active_buffer = self.buffering or bool(self.buffer)
        timeout = self.idle_timeout if active_buffer else CHAR_INTERVAL
        if self.last_char is None or now - self.last_char <= timeout:
            return None
        if active_buffer:
            self.buffering = False
            result = Flush("paste", "".join(self.buffer))
            self.buffer.clear()
            return result
        if self.pending is not None:
            ch, _ = self.pending
            self.pending = None
            return Flush("typed", ch)
        return None

    def newline(self, now):
        if not self.active:
            return False
        if self.pending is not None:
            self.buffer.append(self.pending[0])
            self.pending = None
        self.buffering = True
        self.append("\n", now)
        return True

    def suppress_enter(self, now):
        return self.active or (self.window_until is not None and now <= self.window_until)

    def drain_before_modified(self):
        if not self.active:
            return None
        self.buffering = False
        text = "".join(self.buffer)
        self.buffer.clear()
        if self.pending is not None:
            text += self.pending[0]
            self.pending = None
        return text

    def clear_window(self):
        """Only after draining: preserve no timing across an unrelated key."""
        if self.buffer:
            raise RuntimeError("drain paste buffer before clearing its timing window")
        self.last_char = self.window_until = self.pending = None
        self.count = 0
        self.buffering = False
