"""Turn-owned elapsed time and transient status, independent of model output."""

import asyncio
from time import monotonic

from rich.cells import cell_len, set_cell_size


def format_elapsed(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02}m {seconds:02}s"


class WorkingStatus:
    def __init__(self, invalidate, *, animated=True):
        self.invalidate = invalidate
        self.animated = animated
        self.active = False
        self.paused = False
        self.accumulated = 0.0
        self.started = None
        self.timer = None

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

    def fragments(self, width, *, detail=None, has_draft=False):
        elapsed = self.elapsed
        indicator = "◐◓◑◒"[int(elapsed * 4) % 4] if self.animated else "•"
        header = f" {indicator} Working"
        duration = f" ({format_elapsed(elapsed)})"
        hint = " · ctrl+c clears draft" if has_draft else " · ctrl+c to interrupt"
        if cell_len(header + duration + hint) > width:
            hint = ""
        # Preserve time before spending narrow-terminal width on the label.
        header = set_cell_size(header, max(0, min(cell_len(header), width - cell_len(duration))))
        result = [("bold fg:ansicyan", header), ("bold", duration)]
        if cell_len(header + duration) > width:
            return [("bold", set_cell_size(duration.strip(), max(0, width)).rstrip())]
        if hint:
            result.append(("fg:ansibrightblack", hint))
        if detail:
            detail = " ".join("".join(c for c in detail if c.isprintable() or c.isspace()).split())
            detail = set_cell_size("  └ " + detail, max(0, min(cell_len("  └ " + detail), width)))
            result.append(("fg:ansibrightblack", "\n" + detail))
        return result
