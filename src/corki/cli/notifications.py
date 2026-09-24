"""Focus-aware terminal notifications, independent of any provider or account."""

import asyncio
import os
from collections import deque
from contextlib import contextmanager, suppress


class TerminalNotifications:
    def __init__(self, output, settings, *, interactive, environ=None):
        self.output, self.settings = output, settings
        self.interactive = interactive
        self.focused = True
        self.seen = deque(maxlen=256)
        self.failed = False
        self.pending = None
        self.handle = None
        env = os.environ if environ is None else environ
        # A dumb terminal cannot interpret focus-reporting or OSC sequences.
        self.interactive = interactive and env.get("TERM", "").lower() != "dumb"
        self.tmux = bool(env.get("TMUX"))
        program = env.get("TERM_PROGRAM", "").lower()
        supported = program in {"iterm.app", "ghostty", "wezterm", "warpterminal", "kitty"} or bool(
            env.get("KITTY_WINDOW_ID")
        )
        self.method = settings.notification_method
        if self.method == "auto":
            self.method = "osc9" if supported else "bel"

    def set_focus(self, focused):
        self.focused = focused

    def _write(self, text):
        self.output.write_raw(text)
        self.output.flush()

    def notify(self, kind, identity):
        enabled = self.settings.notifications
        if not self.interactive or self.failed or enabled is False:
            return
        if not isinstance(enabled, bool) and kind not in enabled:
            return
        key = kind, str(identity)
        if key in self.seen:
            return
        self.seen.append(key)
        if self.settings.notification_condition == "unfocused" and self.focused:
            return
        priority = 0 if kind == "agent-turn-complete" else 1
        if self.pending is None or priority >= self.pending[0]:
            self.pending = priority, kind
        if self.handle is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self.post_pending()
            else:
                self.handle = loop.call_soon(self.post_pending)

    def post_pending(self):
        self.handle = None
        pending, self.pending = self.pending, None
        if pending is None or (
            self.settings.notification_condition == "unfocused" and self.focused
        ):
            return
        kind = pending[1]
        message = (
            "Corki task complete" if kind == "agent-turn-complete" else "Corki needs your input"
        )
        sequence = "\a"
        if self.method == "osc9":
            sequence = f"\x1b]9;{message}\a"
            if self.tmux:
                sequence = "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"
        try:
            self._write(sequence)
        except Exception:  # noqa: BLE001 - output backends must not fail the event loop
            self.failed = True  # A broken output must never fail the model turn.

    @contextmanager
    def reporting(self, parsers):
        parsers = tuple(dict.fromkeys(p for p in parsers if p is not None))
        active = self.interactive and bool(self.settings.notifications) and bool(parsers)
        try:
            if active:
                for parser in parsers:
                    parser.focus_listeners.add(self.set_focus)
                try:
                    self._write("\x1b[?1004h")
                except Exception:  # noqa: BLE001 - optional terminal capability
                    self.failed = True
            yield
        finally:
            if self.handle is not None:
                self.handle.cancel()
                self.handle = None
            self.pending = None
            for parser in parsers:
                parser.focus_listeners.discard(self.set_focus)
            if active:
                with suppress(Exception):
                    self._write("\x1b[?1004l")
            self.seen.clear()
            self.focused = True
