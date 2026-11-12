"""Turn-owned elapsed time and transient status, independent of model output."""

import asyncio
from io import StringIO
from time import monotonic

from rich.cells import cell_len, set_cell_size
from rich.console import Console
from rich.text import Text

from corki.cli.display_text import sanitize_user_text
from corki.cli.theme import PROMPT_ACCENT


def format_elapsed(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02}m {seconds:02}s"


def format_work_summary(seconds, width):
    """A one-row Codex-style rule, retaining elapsed time on narrow terminals."""
    width = max(0, width)
    if seconds is None or int(seconds) <= 60:
        return "─" * width
    duration = format_elapsed(seconds)
    label = f"─ Worked for {duration} ─"
    if cell_len(label) > width:
        label = f"─ {duration} ─"
    if cell_len(label) > width:
        label = duration
    if cell_len(label) > width:
        return set_cell_size(label, width)
    return label + "─" * (width - cell_len(label))


class WorkingStatus:
    def __init__(self, invalidate, *, animated=True):
        self.invalidate = invalidate
        self.animated = animated
        self.active = False
        self.paused = False
        self.accumulated = 0.0
        self.started = None
        self.timer = None
        self._details_cache = None

    @property
    def elapsed(self):
        return self.accumulated + (
            max(0.0, monotonic() - self.started) if self.started is not None else 0.0
        )

    def set_active(self, active):
        if self.active == active:
            return
        self.active = active
        self.accumulated = 0.0
        self.started = monotonic() if active and not self.paused else None
        self._refresh()

    def set_paused(self, paused):
        if self.paused == paused:
            return
        self.accumulated = self.elapsed
        self.paused = paused
        self.started = monotonic() if self.active and not paused else None
        self._refresh()

    def _refresh(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.invalidate()
        if self.active and not self.paused:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self.timer = loop.call_later(0.25 if self.animated else 1.0, self._refresh)

    def fragments(self, width, *, detail=None):
        elapsed = self.elapsed
        indicator = "◐◓◑◒"[int(elapsed * 4) % 4] if self.animated else "•"
        header = f" {indicator} Working"
        duration = f" ({format_elapsed(elapsed)})"
        hint = " · esc to interrupt"
        if cell_len(header + duration + hint) > width:
            hint = ""
        # Preserve time before spending narrow-terminal width on the label.
        header = set_cell_size(header, max(0, min(cell_len(header), width - cell_len(duration))))
        result = [(f"bold {PROMPT_ACCENT}", header), ("bold", duration)]
        if cell_len(header + duration) > width:
            return [("bold", set_cell_size(duration.strip(), max(0, width)).rstrip())]
        if hint:
            result.append(("fg:ansibrightblack", hint))
        if detail and width > 4:
            # Codex StatusIndicatorWidget wraps details below the persistent clock.
            # Reuse the measured rows across animation ticks; bound untrusted labels.
            key = (width, detail[:4096], len(detail) > 4096)
            if self._details_cache is None or self._details_cache[0] != key:
                safe = " ".join(sanitize_user_text(key[1]).split())
                console = Console(file=StringIO(), color_system=None)
                rows = Text(safe).wrap(console, width - 4, overflow="fold")
                clipped = len(rows) > 3 or key[2]
                rows = rows[:3]
                if clipped and rows:
                    rows[-1].truncate(max(0, width - 5), overflow="crop")
                    rows[-1].append("…")
                self._details_cache = (key, tuple(row.plain for row in rows))
            for index, row in enumerate(self._details_cache[1]):
                result.append(
                    ("fg:ansibrightblack", "\n" + ("  └ " if index == 0 else "    ") + row)
                )
        return result
